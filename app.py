"""Lesson-planning web app over the database seeded by load_nodes.py and
import_objectives.py. A left sidebar lists courses and, under each, its
Objectives view and its hierarchies. Main views:

- `/<course>/objectives`        a sortable table of the course's raw objectives,
                                a column per hierarchy showing the ids it covers.
- `/<course>/h/<hierarchy>`     the workspace for any hierarchy: its node tree
                                with a droppable zone per node + the raw-objective
                                pool. Editable outlines also edit their structure.

Run:  uv run app.py        (binds HOST:PORT, default 127.0.0.1:5001)
The database path defaults to db.db next to this file; override with LESSON_DB.
"""

import csv
import datetime
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid as uuidlib
from importlib.resources import files as importlib_files

import markdown
from flask import (Flask, Response, abort, flash, g, jsonify, make_response,
                   redirect, render_template, request, session, url_for)
from markupsafe import Markup

# Import sibling repo-root modules (the lesson-planning scripts). The app wires
# their library functions to routes -- it never reimplements their logic -- so the
# CLI and the app stay in lockstep.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)
import calendar_view  # noqa: E402
import collab  # noqa: E402
import course_bundle  # noqa: E402
import hierarchy  # noqa: E402
import seed as seed_module  # noqa: E402
import import_objectives  # noqa: E402
import load_nodes  # noqa: E402
import plan_io  # noqa: E402
import rebuild_db  # noqa: E402
import version  # noqa: E402

DB_PATH = os.environ.get(
    "LESSON_DB", os.path.join(os.path.dirname(__file__), "db.db")
)
# The courses directory: a directory of course directories that is BOTH the load source and
# the export target (markdown hierarchies + objectives.tsv / coverage.tsv per
# course). See FORMAT.md / plan_io.py. In single-user mode it must be a git repo (a
# checkout of your courses repo) so edits autosave + commit there -- COURSES_ROOT is
# resolved below (a plain dir, like the bundled examples/ demo, is copied into a
# throwaway repo). Collab mode manages its own clone, so LESSON_COURSES_DIR is
# single-user only.
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
# Where the calendar view reads bells calendar JSONs (e.g. 'bhs-2025-2026.json').
# Defaults to the data bundled in the `bhs-calendars` PyPI package (whose JSON
# filenames match the calendar ids); override with LESSON_CALENDAR_DIR.
CALENDAR_DIR = os.environ.get(
    "LESSON_CALENDAR_DIR", os.fspath(importlib_files("bhs_calendars") / "data")
)


def _is_courses_repo(d):
    """True if `d` is the TOP of its own git repo (a dedicated courses repo), so
    committing there is safe. False for a plain dir, or a subdir of another repo
    (e.g. the in-repo `courses/`) -- committing there would land in THIS repo."""
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(d)


# Single-user git mode is now the ONLY single-user mode: the courses directory is a git repo
# (a courses-repo checkout) and every edit autosaves + commits there on the
# checked-out branch, authored as the local git user, with no remote push -- the
# single-user analogue of collab. A plain (non-repo) courses directory -- the bundled
# examples/ demo -- is copied into a throwaway tmp git repo at startup so edits
# still commit (to disposable git, never into this engine repo). Off only in collab
# mode (collab owns git). Set LESSON_COURSES_DIR to your courses-repo checkout.
def _ensure_courses_repo(d):
    """Resolve the single-user courses directory to a git repo. If `d` is already the top of
    its own repo, use it in place (real local-git mode). Otherwise (a plain dir,
    e.g. examples/) copy it into a fresh throwaway repo so edits still autosave +
    commit, just to disposable git. Returns (resolved_dir, is_demo)."""
    if _is_courses_repo(d):
        return d, False
    tmp = tempfile.mkdtemp(prefix="lesson-demo-")
    for name in os.listdir(d):
        src = os.path.join(d, name)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(
            src, os.path.join(tmp, name))
    subprocess.run(["git", "init", "-q", tmp], check=True)
    for k, v in (("user.name", "Lesson Planning Demo"),
                 ("user.email", "demo@localhost")):
        subprocess.run(["git", "-C", tmp, "config", k, v], check=True)
    subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
    subprocess.run(["git", "-C", tmp, "commit", "-q", "-m", "Seed demo courses"],
                   check=True)
    return tmp, True


if collab.enabled():
    COURSES_ROOT, LOCAL_GIT, DEMO_MODE = None, False, False
else:
    _courses = os.environ.get("LESSON_COURSES_DIR")
    if not _courses or not os.path.isdir(_courses):
        sys.exit("LESSON_COURSES_DIR must point at a courses git repo (or a plain "
                 "directory to run as a throwaway demo, e.g. "
                 "LESSON_COURSES_DIR=examples). See README.")
    COURSES_ROOT, DEMO_MODE = _ensure_courses_repo(os.path.abspath(_courses))
    LOCAL_GIT = True
LOCAL_AUTOSAVE_SECONDS = int(os.environ.get("LESSON_AUTOSAVE_SECONDS", "2"))

app = Flask(__name__)
# On fly the app runs behind a TLS-terminating proxy that forwards the request as
# http, so trust its X-Forwarded-Proto/Host headers -- otherwise url_for(...,
# _external=True) builds http:// URLs and the OAuth redirect_uri won't match the
# registered https:// callback. Gated on FLY_APP_NAME so we only trust forwarded
# headers when actually behind fly's proxy (never in local dev).
if os.environ.get("FLY_APP_NAME"):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# In single-user (local) mode the secret isn't security-sensitive. In collab mode
# it signs the session cookie that carries the logged-in identity, so it MUST be a
# real secret (set FLASK_SECRET_KEY as a fly secret).
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or (
    secrets.token_hex(32) if collab.enabled() else "lesson-planning-dev")


def db_path():
    """The SQLite cache for the current request: the logged-in user's per-user db
    in collab mode (bound in `before_request`), else the single global db. Falls
    back to the global outside any request context (boot, CLI)."""
    try:
        return g.db_path
    except (RuntimeError, AttributeError):
        return DB_PATH


def courses_root():
    """The courses repo directory for the current request: the logged-in user's git
    worktree in collab mode, else the single global courses repo."""
    try:
        return g.courses_root
    except (RuntimeError, AttributeError):
        return COURSES_ROOT


def db():
    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------
# Collaboration (git-backed multi-user mode). All of this is inert unless
# collab.enabled() -- see collab.py and plans/git-collaboration.md.
# --------------------------------------------------------------------------

# Endpoints reachable without a logged-in session (the auth dance itself).
_AUTH_EXEMPT = {"collab_login", "collab_oauth_start", "collab_callback",
                "collab_devlogin", "collab_logout", "favicon", "static",
                # GitHub webhook: authenticates itself via HMAC signature, not a
                # session -- the login gate must let it through.
                "github_webhook"}

# Human phrases buffered per edit, keyed by mutating endpoint (the route's function
# name); the acting course is interpolated. They compose the Save dialog's suggested
# commit message (collab.suggest_message) -- nothing here commits. Some endpoints
# cover several actions and set g.action_phrase at runtime instead (place,
# node_duration_set, lesson_arrange) -- the entry here is just a fallback. The
# structural ops (_STRUCTURAL_OPS) buffer their own phrase via apply_structural.
_ACTION_PHRASES = {
    "place": "placed objectives in {course}",
    "node_objectives_bulk": "edited node objectives in {course}",
    "unit_new": "added a unit to {course}",
    "unit_rename": "renamed a unit in {course}",
    "unit_delete": "deleted a unit in {course}",
    "unit_arrange": "reordered units in {course}",
    "lesson_new": "added a lesson to {course}",
    "lesson_edit": "edited a lesson in {course}",
    "lesson_part_save": "edited a lesson plan in {course}",
    "lesson_delete": "deleted a lesson in {course}",
    "lesson_arrange": "reordered lessons in {course}",
    "node_duration_set": "set a duration in {course}",
    "objective_new": "added an objective to {course}",
    "objective_edit": "edited an objective in {course}",
    "outline_import": "rebuilt the {course} outline from a reference",
    "references_reorder": "reordered references in {course}",
    "course_rename": "renamed course {course}",
}
# Whole-file save endpoints: they write the course files themselves and record
# their own edit phrase -- the after_request hook skips them.
_SAVE_ENDPOINTS = {"outline_source", "lesson_source"}

# Structural ops that reify to disk immediately (via apply_structural) rather than
# via the debounced file autosave -- they add/remove whole courses or reference
# files, disk reconciliations a single write_course can't express. They still don't
# commit (commit is the explicit Save); they just buffer their own edit phrase.
_STRUCTURAL_OPS = {"course_new", "course_delete", "course_import",
                  "hierarchy_load_course", "hierarchy_delete",
                  "objectives_upload", "hierarchy_upload", "objectives_import_from"}


def git_backed():
    """True when the current request's edits are auto-persisted to git -- collab
    (a signed-in editor) or local single-user git mode."""
    return (collab.enabled() and getattr(g, "editor", False)) or LOCAL_GIT


def _git_target():
    """How to persist the current edit to git, or None if not git-backed. A dict:
    repo (dir to commit), db (for write_course), author ((name,email) or None for
    the ambient git identity), push_key (handle to push, or None), key (debounce /
    action-buffer bucket), delay (autosave debounce seconds). Read in the request
    so an autosave flush built from it can run later without `g`.
      - collab editor: their worktree + db, teacher author, push enabled;
      - local-git:     the courses repo + main db, ambient author, no push."""
    if collab.enabled() and getattr(g, "editor", False):
        u = g.user or {}
        return {"repo": g.courses_root, "db": g.db_path,
                "author": (u.get("name"), u.get("email")),
                "push_key": g.handle, "key": g.handle,
                "delay": collab.autosave_seconds()}
    if LOCAL_GIT:
        return {"repo": COURSES_ROOT, "db": DB_PATH, "author": None,
                "push_key": None, "key": "_local", "delay": LOCAL_AUTOSAVE_SECONDS}
    return None


def _record_edit(course, phrase):
    """Buffer an edit `phrase` (for the Save dialog's suggested message) and mark the
    workspace dirty -- for the ops that bypass the after_request hook (the structural
    ops and the whole-file save endpoints). No-op when not git-backed."""
    t = _git_target()
    if not t:
        return
    if phrase:
        collab.record_action(t["key"], phrase)
    collab.mark_dirty(t["key"])


def apply_structural(course, message, *, drop_course=False, remove_files=()):
    """Reify a structural change to the git-backed course FILES immediately -- create,
    delete, or import a course; add or remove a reference -- because these are disk
    reconciliations a later write_course can't express (a deleted course dir, a
    removed reference file). It does NOT commit: commit is the explicit Save. Buffers
    `message` as the edit phrase and marks the workspace dirty. `remove_files` are
    paths relative to the course dir to delete; `drop_course` removes the whole
    course dir (and skips write_course). No-op when not git-backed."""
    t = _git_target()
    if not t:
        return
    course_dir = os.path.join(t["repo"], course)
    for rel in remove_files:
        try:
            os.remove(os.path.join(course_dir, rel))
        except FileNotFoundError:
            pass
    if drop_course:
        shutil.rmtree(course_dir, ignore_errors=True)
    else:
        plan_io.write_course(t["db"], course, course_dir)
    _record_edit(course, message)


def current_user():
    return session.get("user") if collab.enabled() else None


@app.before_request
def _collab_gate():
    """In collab mode: require a session, bind the per-user (db, courses directory), and
    block writes from viewers. A no-op in single-user mode."""
    if not collab.enabled():
        return
    ep = request.endpoint
    if ep in _AUTH_EXEMPT:
        return
    user = current_user()
    if not user:
        if request.method != "GET":
            abort(401, "sign in to continue")
        return redirect(url_for("collab_login", next=request.url))
    g.user = user
    g.handle = user["handle"]
    g.role = user["role"]
    g.editor = (user["role"] == "editor")
    try:
        if g.editor:
            g.db_path, g.courses_root = collab.editor_binding(
                user["handle"], user.get("name"), user.get("email"))
        else:
            g.db_path, g.courses_root = collab.viewer_binding()
    except Exception as e:
        print(f"collab: binding failed for {user['handle']}: {e}", file=sys.stderr)
        abort(500, "couldn't open your workspace")
    # Viewers are strictly read-only: reject any mutating request.
    if request.method not in ("GET", "HEAD", "OPTIONS") and not g.editor:
        abort(403, "read-only access")


@app.after_request
def _autocommit_edit(resp):
    """After an edit: buffer its phrase (for the Save suggestion), mark the workspace
    dirty, and schedule the debounced autosave that writes the db to the course
    FILES -- no commit. Commit is the explicit Save; the timer only keeps disk in
    sync with the db (the db is a cache). No-op when not git-backed, or when the op
    records itself (the structural ops and whole-file save endpoints)."""
    if not (request.method == "POST" and resp.status_code < 400):
        return resp
    t = _git_target()
    if not t:
        return resp
    ep = request.endpoint
    if ep in _SAVE_ENDPOINTS or ep in _STRUCTURAL_OPS:
        return resp
    course = (request.view_args or {}).get("course")
    # A handler may set g.action_phrase to describe what it actually did when one
    # endpoint covers several actions (e.g. `place` maps vs. unmaps); otherwise
    # fall back to the static per-endpoint phrase. A truthy phrase also marks this
    # as a real edit (so /sync, /save, /flush etc. don't dirty the workspace).
    phrase = getattr(g, "action_phrase", None)
    if phrase is None and ep in _ACTION_PHRASES:
        phrase = _ACTION_PHRASES[ep].format(course=course or "the course")
    if phrase:
        collab.record_action(t["key"], phrase)
        collab.mark_dirty(t["key"])
        if course:
            # Capture the target now so the timer thread needs no request context.
            repo, db = t["repo"], t["db"]

            def flush(c, repo=repo, db=db):
                # Don't clobber files that changed under us (an external pull): skip
                # the write and flag a conflict for the UI to surface. The edit stays
                # in the db; Reload resolves it by taking the disk version.
                if collab.head_moved(repo):
                    collab.flag_conflict(repo)
                    return
                plan_io.write_course(db, c, os.path.join(repo, c))

            collab.schedule_autosave(t["key"], t["delay"], course, flush)
    return resp


@app.context_processor
def inject_collab():
    """Template flags: whether the current user may edit (always true in
    single-user mode), their identity, and any pending-push state."""
    if not collab.enabled():
        # Single-user local-git (and the throwaway-repo demo): edits autosave to
        # FILES on a timer, but committing is the explicit Save button.
        return {"collab_enabled": False, "can_edit": True, "collab_user": None,
                "git_backed": LOCAL_GIT,
                "dirty": LOCAL_GIT and collab.is_dirty("_local"),
                "conflict": LOCAL_GIT and collab.has_conflict(COURSES_ROOT)}
    editor = getattr(g, "editor", False)
    handle = getattr(g, "handle", None)
    published, pending = collab.push_status(handle) if editor and handle else (True, 0)
    return {
        "collab_enabled": True,
        "git_backed": editor,
        "can_edit": editor,
        "dirty": editor and handle and collab.is_dirty(handle),
        "conflict": bool(editor and getattr(g, "courses_root", None)
                         and collab.has_conflict(g.courses_root)),
        "collab_user": getattr(g, "user", None),
        "collab_role": getattr(g, "role", None),
        "collab_pending": pending,
        "collab_branch_published": published,
        "collab_push_error": collab.push_error(handle) if handle else None,
    }


# ---- Auth + sync routes (collab mode) ------------------------------------

@app.route("/login")
def collab_login():
    if not collab.enabled():
        return redirect(url_for("index"))
    if current_user():
        return redirect(url_for("index"))
    session["next"] = request.args.get("next") or url_for("index")
    return render_template("login.html", dev_login=collab.dev_login_enabled(),
                           page_title="Sign in")


@app.route("/login/start")
def collab_oauth_start():
    if not collab.enabled():
        abort(404)
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    redirect_uri = url_for("collab_callback", _external=True)
    return redirect(collab.oauth_authorize_url(state, redirect_uri))


@app.route("/oauth/callback")
def collab_callback():
    if not collab.enabled():
        abort(404)
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400, "bad OAuth state")
    code = request.args.get("code")
    if not code:
        abort(400, "no OAuth code")
    redirect_uri = url_for("collab_callback", _external=True)
    try:
        token = collab.oauth_exchange(code, redirect_uri)
        handle, name, email = collab.github_user(token)
    except Exception as e:
        abort(502, f"GitHub sign-in failed: {e}")
    return _finish_login(handle, name, email)


@app.route("/login/dev", methods=["POST"])
def collab_devlogin():
    if not collab.dev_login_enabled():
        abort(404)
    handle = (request.form.get("handle") or "").strip()
    return _finish_login(handle, handle, collab._noreply(handle or "dev"))


def _finish_login(handle, name, email):
    role = collab.role_of(handle)
    if not role:
        return render_template("login.html", dev_login=collab.dev_login_enabled(),
                               denied=handle, page_title="Sign in"), 403
    session["user"] = {"handle": handle, "name": name, "email": email,
                       "role": role}
    nxt = session.pop("next", None) or url_for("index")
    # Returning editors: make sure the sandbox exists, then pull in anything
    # merged to main since last time. (Auth routes skip the before_request
    # binding, so create it here before syncing.)
    if role == "editor":
        try:
            # Ensure the sandbox exists, then sync: merge anything landed on main
            # since last time AND push the branch (establishing origin/<handle> on a
            # brand-new sandbox). sync pushes synchronously, so nothing more to do.
            collab.editor_binding(handle, name, email)
            collab.sync(handle, name, email)
        except Exception as e:
            print(f"collab: login sync failed for {handle}: {e}", file=sys.stderr)
    return redirect(nxt)


@app.route("/logout")
def collab_logout():
    session.clear()
    return redirect(url_for("collab_login"))


def _write_all_courses(t):
    """Write every db course's files to the git-backed courses dir (the synchronous
    flush Save/Sync do before committing/merging). Cancels the pending file-autosave
    first so it doesn't race."""
    collab.cancel_autosave(t["key"])
    with db() as conn:
        courses = [r["course"] for r in
                   conn.execute("SELECT course FROM courses ORDER BY course")]
    for c in courses:
        plan_io.write_course(t["db"], c, os.path.join(t["repo"], c))


def _diff_lines(text):
    """Classify a unified diff into [{cls, text}] for the commit page's rendering:
    `file`/`hunk` headers, `add`/`del` lines, `ctx` (context) for the rest."""
    out = []
    for line in text.splitlines():
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "new file",
                            "deleted file", "rename ", "similarity ", "old mode",
                            "new mode")):
            cls = "file"
        elif line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = "ctx"
        out.append({"cls": cls, "text": line})
    return out


@app.route("/commit")
def commit_page():
    """The Commit page: a commit-message box on top and the diff that a commit would
    make below. Reifies the latest db to files so the diff is current; if the files
    changed on disk under us (an external pull), shows the conflict instead. Submits
    to /save."""
    back = request.referrer or url_for("index")
    if back.rstrip("/").endswith("/commit"):   # a refresh: don't loop back here
        back = url_for("index")
    t = _git_target()
    if not t:
        flash("Nothing to commit here.")
        return redirect(back)
    conflict = collab.head_moved(t["repo"])
    diff = []
    if conflict:
        collab.flag_conflict(t["repo"])
    else:
        _write_all_courses(t)            # reify so the diff reflects the live db
        diff = _diff_lines(collab.working_diff(t["repo"]))
    return render_template("commit.html", page_title="Commit changes", back=back,
                           message=collab.suggest_message(t["key"], "Update courses"),
                           diff=diff, conflict=conflict)


@app.route("/save", methods=["POST"])
def save_courses():
    """Reify the latest db state to the course files and COMMIT them with the
    user-supplied message; in collab mode push to GitHub immediately. The explicit
    counterpart to the file-only autosave timer -- the one place a commit happens.
    Posted from the Commit page (with a `next` to return to)."""
    back = redirect(request.form.get("next") or request.referrer or url_for("index"))
    t = _git_target()
    if not t:
        flash("Nothing to commit here.")
        return back
    # Files changed under us (an external pull)? Committing would write the db over
    # them -- refuse and point at Reload (which takes the disk version).
    if collab.head_moved(t["repo"]):
        collab.flag_conflict(t["repo"])
        flash("The course files changed on disk (a pull?). "
              + ("Sync" if collab.enabled() else "Reload")
              + " to take the new version first — your in-app edits since then will be lost.")
        return back
    message = (request.form.get("message") or "").strip() \
        or collab.suggest_message(t["key"], "Update courses")
    _write_all_courses(t)
    try:
        subject = collab.commit_repo(t["repo"], message, author=t["author"], push_key=None)
    except Exception as e:
        flash(f"Save failed: {e}")
        return back
    collab.clear_dirty(t["key"])
    if subject is None:
        flash("Nothing to commit — no changes since the last commit.")
        return back
    collab.clear_actions(t["key"])
    if collab.enabled() and getattr(g, "editor", False):
        ok, out = collab.push_sync(g.handle)
        flash(f"Committed and pushed: {subject}" if ok
              else f"Committed {subject!r}, but the push failed: {out}")
    else:
        flash(f"Committed: {subject}")
    return back


@app.route("/flush", methods=["POST"])
def flush_courses():
    """Write any debounced-but-unwritten edits to the course files now -- called via a
    `beforeunload` beacon so the latest edit is on disk the instant you leave. 204."""
    t = _git_target()
    if t:
        collab.flush_pending(t["key"])
    return ("", 204)


@app.route("/savebar")
def savebar():
    """The Commit button + live status fragment, polled by the sidebar so the
    'uncommitted' hint stays current after htmx edits (which don't re-render the
    sidebar). The poll also proactively notices an external change (a pull moved
    HEAD) so the conflict warning shows within a poll even before the next edit.
    Both modes; empty for non-editors."""
    t = _git_target()
    if t and collab.head_moved(t["repo"]):
        collab.flag_conflict(t["repo"])
    return render_template("_savebar.html")




@app.route("/sync", methods=["POST"])
def sync_courses():
    """Pull in the latest course content. Collab: merge origin/main into the editor's
    branch (per-user) -- which needs a clean tree, so refuse while there are unsaved
    (uncommitted) edits. Single-user: reload every course from its files on disk (the
    git-tracked courses dir; do the git pull yourself, this re-reads what's there)."""
    back = redirect(request.referrer or url_for("index"))
    if collab.enabled():
        if not getattr(g, "editor", False):
            abort(403)
        u = g.user
        t = _git_target()
        # Flush pending writes so the dirty check sees the true state (unless the
        # tree already moved under us -- then don't clobber it), then refuse if
        # anything is uncommitted: Sync only pulls others' work; committing is Save.
        if not collab.head_moved(t["repo"]):
            _write_all_courses(t)
        if collab.has_uncommitted(g.courses_root):
            flash("You have unsaved changes — Commit them before syncing.")
            return back
        try:
            result = collab.sync(u["handle"], u.get("name"), u.get("email"))
        except Exception as e:
            flash(f"Sync failed: {e}")
            return back
        if result.get("conflict"):
            flash(f"{result['message']} Files: {', '.join(result.get('files', []))}")
        else:
            flash(result["message"])
        return back
    # Single-user: flush db -> files first (so unsaved edits aren't lost to the
    # reload) -- UNLESS the files changed under us (an external pull), in which case
    # Reload is the resolution: take the disk version, don't clobber it.
    t = _git_target()
    if t and not collab.head_moved(t["repo"]):
        _write_all_courses(t)
    try:
        dirs = seed_module.course_dirs(courses_root())
        seed_module.load_courses(db_path(), courses_root())
    except (OSError, ValueError) as e:
        flash(f"Couldn't reload from git: {e}")
        return back
    collab.remember_head(courses_root())   # db now reflects disk at the current HEAD
    names = ", ".join(os.path.basename(d) for d in dirs) or "none"
    flash(f"Reloaded {len(dirs)} course(s) from git: {names}")
    return back


@app.route("/github/webhook", methods=["POST"])
def github_webhook():
    """GitHub push webhook: refresh the shared read-only main view when course
    content is pushed to origin/main, so external edits show up near-instantly
    instead of waiting on the background poll (collab._start_main_timer). Collab
    mode only; inert (404) in single-user/local mode or with no secret set.

    Security is the HMAC-SHA256 signature over the raw body, keyed by
    LESSON_GITHUB_WEBHOOK_SECRET (GitHub's `X-Hub-Signature-256`); without it
    anyone could POST here to force rebuilds. We do our own auth, so the endpoint
    is in `_AUTH_EXEMPT` (the collab login gate would otherwise bounce GitHub).

    The refresh runs off-thread and we ack `202` immediately: GitHub times out
    deliveries at ~10s and a full rebuild_db(MAIN) can approach that. refresh_main
    is already serialized under collab's _main_lock, so an overlapping delivery and
    timer tick are safe, and a fetch with no new commits is cheap (no rebuild
    unless HEAD actually moved)."""
    secret = os.environ.get("LESSON_GITHUB_WEBHOOK_SECRET")
    if not (collab.enabled() and secret):
        abort(404)
    expected = "sha256=" + hmac.new(
        secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
        abort(401)
    if (request.headers.get("X-GitHub-Event") == "push"
            and (request.get_json(silent=True) or {}).get("ref") == "refs/heads/main"):
        threading.Thread(target=collab.refresh_main, daemon=True,
                         name="github-webhook-refresh").start()
    return ("", 202)


def _schema_version():
    """The schema's `PRAGMA user_version` value (the canonical schema stamps every
    db with it). Single source of truth: schema.sql."""
    m = re.search(r"PRAGMA\s+user_version\s*=\s*(\d+)", open(SCHEMA_PATH).read())
    return int(m.group(1)) if m else 0


def ensure_schema():
    """Apply the canonical schema to a fresh/empty db, and discard a stale one.

    A first run (no db.db) boots into a valid, empty database -- ready to be
    populated (load a reference, restore a snapshot) instead of dead-ending at
    "no courses loaded". The db is a disposable cache, never migrated in place; a
    db.db left over from an OLDER schema (its `PRAGMA user_version` doesn't match
    schema.sql) is deleted here so the schema is re-applied fresh and the startup
    `seed` rebuilds it from the courses directory -- rather than 500ing on a missing column.
    (Any course that lived only in the db and was never saved to git is
    lost; the courses directory is the source of truth.)
    """
    if os.path.exists(DB_PATH):
        with db() as conn:
            populated = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='courses'").fetchone())
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if populated and version != _schema_version():
            print(f"db.db is schema v{version}, current is v{_schema_version()}; "
                  f"discarding it and rebuilding from the courses directory.", file=sys.stderr)
            os.remove(DB_PATH)
    with db() as conn:
        if "courses" not in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            conn.executescript(open(SCHEMA_PATH).read())   # PRAGMA user_version included
            conn.commit()


def courses(conn):
    return [r["course"] for r in conn.execute(
        "SELECT course FROM courses ORDER BY course")]


# A course is backed by hierarchies (the courses->hierarchies link). Its OUTLINE
# (the authored lesson plan) is the course's explicit primary_outline pointer,
# falling back to its single editable hierarchy. Both reference "coverage" and
# lesson "placement" are coverage edges into a hierarchy.

def outline_hierarchy(conn, course):
    """The bare slug of the course's official outline hierarchy, or None.
    `courses.primary_outline` is the sole authority; the editable fallback only
    matters for a half-built course with no pointer set yet."""
    row = conn.execute("SELECT primary_outline FROM courses WHERE course=?",
                       (course,)).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 "
        "ORDER BY hierarchy LIMIT 1", (course,)).fetchone()
    return row[0] if row else None


def ensure_outline(conn, course):
    """The course's outline hierarchy slug, creating + registering it if needed.

    Assumes the course already exists -- creating courses is `course_new`'s job
    (it validates the id first). Callers operate on a course the request is
    already scoped to; never call this for an unvalidated/unknown course, or it
    would mint an outline (and previously a course) for junk like a browser's
    /apple-touch-icon.png probe."""
    O = outline_hierarchy(conn, course)
    if not O:
        O = "plan"  # course-relative slug; identity is (course, 'plan')
        conn.execute("INSERT OR IGNORE INTO hierarchies(course, hierarchy, editable, title,"
                     " source) VALUES (?, ?, 1, 'Course outline', NULL)",
                     (course, O))
        # Measure the plan against each of the course's references (ordered).
        conn.execute(
            "INSERT OR IGNORE INTO hierarchy_targets(course, outline, reference, position)"
            " SELECT ?, ?, hierarchy, ROW_NUMBER() OVER (ORDER BY hierarchy) - 1"
            " FROM hierarchies WHERE course=? AND editable=0", (course, O, course))
    # Make it the course's official outline if one isn't set yet.
    conn.execute("UPDATE courses SET primary_outline=? WHERE course=? AND primary_outline IS NULL",
                 (O, course))
    return O


def _pin_slug(text, slug):
    """Return `text` with its front matter's `slug:` set to `slug` (the bare,
    course-relative identity). Replaces any existing slug line; inserts one right
    after the opening `---`. Leaves text without front matter untouched."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    body = [l for l in lines[1:end] if l.split(":", 1)[0].strip() != "slug"]
    return "".join([lines[0], f"slug: {slug}\n"] + body + lines[end:])


@app.context_processor
def inject_version():
    """The git SHA the server was built from, for the sidebar footer."""
    return {"git_sha": version.git_sha()}


@app.context_processor
def inject_nav():
    """Sidebar data for every page: courses, each with its course outline pulled
    out (elevated as the first item) and its other hierarchies, plus the
    course/hierarchy the current request is showing."""
    nav = []
    va = request.view_args or {}
    nav_course = va.get("course")
    active = va.get("hierarchy")  # set only on the workspace (hierarchy_view)
    try:
        with db() as conn:
            cs = conn.execute(
                "SELECT course, title, primary_outline FROM courses "
                "ORDER BY course").fetchall()
            by_course = {}
            for h in conn.execute(
                "SELECT h.hierarchy, h.course, h.editable, h.title FROM hierarchies h "
                "LEFT JOIN hierarchy_targets t ON t.course=h.course AND t.reference=h.hierarchy "
                "ORDER BY h.course, h.editable, t.position, h.hierarchy"):
                by_course.setdefault(h["course"], []).append(
                    {"hierarchy": h["hierarchy"],
                     "editable": h["editable"], "label": h["title"]})
            for c in cs:
                hs = by_course.get(c["course"], [])
                outline = c["primary_outline"] or next(
                    (h["hierarchy"] for h in hs if h["editable"]), None)
                refs = [h for h in hs if h["hierarchy"] != outline]
                # Empty outline -> offer the build-from-hierarchy button in the
                # sidebar; once it has content, that moves to the settings page.
                outline_empty = True
                if outline:
                    outline_empty = conn.execute(
                        "SELECT 1 FROM nodes WHERE course=? AND hierarchy=? LIMIT 1",
                        (c["course"], outline)).fetchone() is None
                nav.append({"course": c["course"], "title": c["title"], "outline": outline,
                            "hierarchies": refs, "outline_empty": outline_empty})
    except sqlite3.OperationalError:
        pass
    return {"course_nav": nav, "active_hierarchy": active, "nav_course": nav_course}


def leaf_status(node, objectives_by_node, planned_leaves):
    if node["node_id"] in planned_leaves:
        return "planned"
    if objectives_by_node.get(node["node_id"]):
        return "objective"
    return "gap"


def build_tree(nodes, objectives_by_node, planned_leaves, gaps_only=False):
    """Build a nested tree of view dicts; optionally prune to gap leaves + ancestors."""
    by_id = {n["node_id"]: n for n in nodes}
    children = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)

    keep = None
    if gaps_only:
        keep = set()
        for n in nodes:
            if n["is_leaf"] and leaf_status(n, objectives_by_node, planned_leaves) == "gap":
                nid = n["node_id"]
                while nid is not None and nid not in keep:
                    keep.add(nid)
                    nid = by_id[nid]["parent_id"] if nid in by_id else None

    def make(n):
        nid = n["node_id"]
        kids = [make(c) for c in children.get(nid, [])
                if keep is None or c["node_id"] in keep]
        return {
            "id": nid,
            "level": n["level"],
            "label": (n["text"] or "").split("\n", 1)[0],
            "text": n["text"] or "",
            "is_leaf": bool(n["is_leaf"]),
            "status": leaf_status(n, objectives_by_node, planned_leaves)
                      if n["is_leaf"] else None,
            "objectives": objectives_by_node.get(nid, []),
            "children": kids,
        }

    return [make(n) for n in children.get(None, [])
            if keep is None or n["node_id"] in keep]


def synthetic_ids(nodes):
    """Positional display ids for a hierarchy whose node_ids are uuids (an outline):
    pre-order '1', '1.1', '1.2', '2.1', ... reflecting the current structure.

    Returns {node_id: (display, seq)} -- display is the dotted id; seq is a global
    pre-order index for document-order sorting. `nodes` rows need node_id/parent_id/
    ordinal. Identity stays the uuid; these are computed fresh each render.
    """
    children = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: (n["ordinal"], n["node_id"]))
    out, seq = {}, [0]

    def walk(parent, prefix):
        for i, n in enumerate(children.get(parent, []), 1):
            if n["node_id"] in out:  # guard against a parent cycle
                continue
            disp = f"{prefix}.{i}" if prefix else str(i)
            out[n["node_id"]] = (disp, seq[0])
            seq[0] += 1
            walk(n["node_id"], disp)

    walk(None, "")
    # Orphans (parent_id points to a missing/unreachable node) still need an id so
    # callers can look up every node; give them a "?"-prefixed id at the end.
    for i, n in enumerate((n for n in nodes if n["node_id"] not in out), 1):
        out[n["node_id"]] = (f"?{i}", seq[0])
        seq[0] += 1
    return out


INLINE = re.compile(r"`([^`]+)`|\*([^*]+)\*")


def _inline(text):
    """Escape HTML, then render markdown `code` and *emphasis* inline -> str."""
    return INLINE.sub(
        lambda m: f"<code>{html.escape(m.group(1))}</code>" if m.group(1)
        else f"<em>{html.escape(m.group(2))}</em>",
        html.escape(text or ""),
    )


@app.template_filter("inline")
def inline(text):
    return Markup(_inline(text))


# Block-level markdown for lesson-plan part content (multi-paragraph free text:
# paragraphs, lists, sub-headings, fenced code, tables). One reusable converter,
# reset() per call. Authored by the course's own teachers (trusted), like the help
# page, so raw HTML in the source is passed through rather than sanitized.
_MD = markdown.Markdown(extensions=["extra", "sane_lists"], output_format="html5")


def render_markdown(text):
    """Render a block of markdown to HTML (safe Markup for templates)."""
    _MD.reset()
    return Markup(_MD.convert(text or ""))


@app.route("/")
def index():
    with db() as conn:
        cs = courses(conn)
    if not cs:
        return redirect(url_for("data"))  # empty db: land on the setup/Data page
    return redirect(url_for("plan", course=cs[0]))   # land on the course outline


@app.route("/favicon.ico")
def favicon():
    """Serve the favicon at the root too, for browsers' automatic /favicon.ico probe."""
    return app.send_static_file("favicon.ico")


@app.route("/help")
def help_page():
    """A static explainer: the data model, the lifecycle, and how to add material."""
    with db() as conn:
        rows = conn.execute(
            "SELECT c.course, c.title, "
            "  (SELECT count(*) FROM hierarchies h WHERE h.course=c.course AND h.editable=0) refs, "
            "  (SELECT count(*) FROM hierarchies h WHERE h.course=c.course AND h.editable=1) plans "
            "FROM courses c ORDER BY c.course").fetchall()
    cs = [r["course"] for r in rows]
    return render_template("help.html", courses=cs, course=(cs[0] if cs else None),
                           course_rows=rows)


# --------------------------------------------------------------------------
# Data: bootstrap & populate from the courses directory / version control. These wire the
# load_nodes / plan_io / seed library functions to the UI so the whole lifecycle
# -- start empty, load real data, export markdown -- happens in the app.

@app.route("/data")
def data():
    """Settings page: how the on-disk courses directory relates to the app (export + the
    sidebar Sync). Creating a course is the sidebar (+); adding a reference
    hierarchy and exporting/deleting a course live on the per-course settings page
    (the gear). Also the empty-db landing page (see `index`). Reloading from the
    courses directory is the sidebar Sync button."""
    with db() as conn:
        cs = conn.execute("SELECT course, title FROM courses ORDER BY course").fetchall()
    return render_template("data.html", courses=cs,
                           export_dir=os.path.relpath(courses_root(), REPO_ROOT),
                           page_title="Settings")


# --------------------------------------------------------------------------
# Course-first setup: create a course, then manage its hierarchies on a
# per-course Setup page (the sidebar drives this; see templates/base.html).

COURSE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


@app.route("/course/new", methods=["POST"])
def course_new():
    """Create an empty course (id + title) and give it an outline straight away,
    so it's immediately complete and shows in the sidebar. Course id is the
    /<course> URL slug: lowercase letters, digits, hyphens."""
    course = (request.form.get("course") or "").strip().lower()
    title = (request.form.get("title") or "").strip()
    back = request.referrer or url_for("index")
    if not course:
        flash("Course id is required.")
        return redirect(back)
    if not COURSE_ID_RE.match(course):
        flash(f"Invalid course id {course!r}: use lowercase letters, digits, and hyphens.")
        return redirect(back)
    with db() as conn:
        if conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            flash(f"Course {course!r} already exists.")
            return redirect(url_for("tree", course=course))
        conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)",
                     (course, title or course.upper()))
        ensure_outline(conn, course)
    apply_structural(course, f"Create course {course}")
    # The new course appears in the sidebar; land on it (its empty outline).
    return redirect(url_for("tree", course=course))


def _hierarchy_confirm(course, text, filename, slug=None, title=None,
                       mode=None, error=None):
    """Render the upload confirmation page: the parsed summary plus the editable
    slug / title, so the user fixes the bare slug before it's committed. Re-used
    for the validation/collision error re-render. Returns a redirect if the
    markdown won't parse at all."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    try:
        doc = load_nodes.parse(text)
    except (Exception, SystemExit) as e:  # unparseable / missing levels: or title:
        flash(f"Could not load {filename!r}: {e}")
        return redirect(url_for("tree", course=course))
    slug = (slug if slug is not None else doc.get("slug") or stem).strip().lower()
    with db() as conn:
        existing = sorted(r["hierarchy"] for r in conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0", (course,)))
        outline = outline_hierarchy(conn, course)
    return render_template(
        "hierarchy_confirm.html", course=course, filename=filename, text=text,
        slug=slug, title=title if title is not None else doc.get("title"),
        levels=doc.get("levels"), node_count=len(doc["nodes"]),
        root=(doc["nodes"][0]["id"] if doc["nodes"] else None),
        existing=existing, outline=outline, mode=mode, error=error,
        page_title=f"Add a reference to {course.upper()}")


@app.route("/<course>/hierarchy/prepare", methods=["POST"])
def hierarchy_prepare(course):
    """Step 1 of a reference upload: parse the chosen .md and show the confirm page
    (editable bare slug / title) rather than committing immediately."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
    back = request.referrer or url_for("tree", course=course)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(back)
    return _hierarchy_confirm(course, f.read().decode("utf-8", "replace"), f.filename)


@app.route("/<course>/hierarchy/load", methods=["POST"])
def hierarchy_load_course(course):
    """Step 2 (commit): load the confirmed reference markdown into THIS course and
    persist it to the courses directory as `{slug}.md` with the bare slug pinned in the front
    matter. The slug/title come from the confirm form; `mode` ('add'|'replace')
    resolves a slug that already names a reference in the course."""
    with db() as conn:
        crow = conn.execute("SELECT title FROM courses WHERE course=?", (course,)).fetchone()
        if not crow:
            abort(404)
    text = request.form.get("text") or ""
    filename = request.form.get("filename") or "upload.md"
    over = lambda k: (request.form.get(k) or "").strip() or None
    mode = request.form.get("mode")
    try:
        doc = load_nodes.parse(text)
    except (Exception, SystemExit) as e:
        flash(f"Could not load {filename!r}: {e}")
        return redirect(url_for("tree", course=course))
    title = over("title") or doc.get("title")
    stem = os.path.splitext(os.path.basename(filename))[0]
    slug = (over("hierarchy") or doc.get("slug") or stem).strip().lower()
    confirm = lambda err: _hierarchy_confirm(course, text, filename, over("hierarchy") or slug,
                                             title, mode, err)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        return confirm(f"Invalid slug {slug!r}: use lowercase letters, digits, and hyphens.")
    with db() as conn:
        row = conn.execute("SELECT editable FROM hierarchies WHERE course=? AND hierarchy=?",
                           (course, slug)).fetchone()
    if row and row["editable"]:
        return confirm(f"{slug!r} is the course outline — choose a different slug.")
    if row and mode != "replace":
        return confirm(f"A reference {slug!r} already exists. Choose Replace, "
                       f"or rename the slug to add it as a new hierarchy.")
    text = _pin_slug(text, slug)   # author the bare slug into the front matter
    rows = load_nodes.build_rows(course, slug, doc["nodes"])
    # Persist the markdown into the courses directory as <slug>.md (the load source of truth).
    course_dir = os.path.join(courses_root(), course)
    os.makedirs(course_dir, exist_ok=True)
    with open(os.path.join(course_dir, f"{slug}.md"), "w", encoding="utf-8") as out:
        out.write(text if text.endswith("\n") else text + "\n")
    # Re-loading replaces this hierarchy's nodes; warn (don't drop) about coverage
    # edges into ids the new version no longer has (a renamed/removed id surfaces).
    new_ids = {r[2] for r in rows}
    with db() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT DISTINCT node_id FROM coverage WHERE course=? AND hierarchy=?",
            (course, slug))}
    orphaned = sorted(existing - new_ids)
    load_nodes.load(db_path(), slug, course, crow["title"],
                    rows, source=filename, title=title, source_md=text)
    # Measure the course outline against this new reference (the eager outline was
    # created before any reference existed, so link it here).
    with db() as conn:
        O = outline_hierarchy(conn, course)
        if O:
            # Append the new reference to the course's ordered targets (a re-upload
            # of an existing slug keeps its position via OR IGNORE).
            conn.execute(
                "INSERT OR IGNORE INTO hierarchy_targets(course, outline, reference, position)"
                " SELECT ?, ?, ?, COALESCE(MAX(position), -1) + 1 FROM hierarchy_targets"
                " WHERE course=? AND outline=?", (course, O, slug, course, O))
    # The loaded hierarchy shows up in the setup table, so only surface the
    # non-obvious case: coverage edges now pointing at ids the new version dropped.
    if orphaned:
        flash(f"Loaded {slug!r}, but {len(orphaned)} existing coverage edge(s) now "
              f"point to node ids not in this version: {', '.join(orphaned[:6])}"
              f"{'…' if len(orphaned) > 6 else ''}")
    apply_structural(course, f"Add reference {slug} to {course}")
    # Land on the loaded hierarchy so the upload (from the sidebar or setup) shows.
    return redirect(url_for("hierarchy_view", course=course, hierarchy=slug))


@app.route("/<course>/hierarchy/<hierarchy>/delete", methods=["POST"])
def hierarchy_delete(course, hierarchy):
    """Delete one of a course's reference hierarchies and everything anchored to
    it: its nodes, the coverage edges into it, per-node attrs, and any
    outline<->reference target rows. The outline isn't deletable here."""
    with db() as conn:
        row = conn.execute(
            "SELECT editable FROM hierarchies WHERE hierarchy=? AND course=?",
            (hierarchy, course)).fetchone()
        if not row:
            abort(404)
        if row["editable"]:
            flash("The course outline can't be deleted here.")
            return redirect(url_for("tree", course=course))
        n = conn.execute("SELECT count(*) FROM coverage WHERE course=? AND hierarchy=?",
                         (course, hierarchy)).fetchone()[0]
        conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=?", (course, hierarchy))
        conn.execute("DELETE FROM node_attr WHERE course=? AND hierarchy=?", (course, hierarchy))
        conn.execute("DELETE FROM node_duration WHERE course=? AND hierarchy=?", (course, hierarchy))
        conn.execute("DELETE FROM hierarchy_targets WHERE course=? AND (outline=? OR reference=?)",
                     (course, hierarchy, hierarchy))
        conn.execute("DELETE FROM nodes WHERE course=? AND hierarchy=?", (course, hierarchy))
        conn.execute("DELETE FROM hierarchies WHERE course=? AND hierarchy=?", (course, hierarchy))
    apply_structural(course, f"Remove reference {hierarchy} from {course}",
                      remove_files=[f"{hierarchy}.md"])
    flash(f"Deleted hierarchy {hierarchy!r} ({n} coverage edge(s) removed).")
    return redirect(url_for("tree", course=course))


@app.route("/<course>/references/reorder", methods=["POST"])
def references_reorder(course):
    """Persist a drag-reordered sidebar reference list: `ids` is the new order of
    reference slugs; write each one's hierarchy_targets.position."""
    with db() as conn:
        O = outline_hierarchy(conn, course)
        for pos, slug in enumerate(_id_list("ids")):
            conn.execute("UPDATE hierarchy_targets SET position=? "
                         "WHERE course=? AND outline=? AND reference=?", (pos, course, O, slug))
        conn.commit()
    return ("", 204)


@app.route("/<course>/rename", methods=["POST"])
def course_rename(course):
    """Rename a course's display title (the id/slug is fixed). Posted from the
    course settings page."""
    title = (request.form.get("title") or "").strip()
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        if title:  # ignore an empty title rather than blanking it
            conn.execute("UPDATE courses SET title=? WHERE course=?", (title, course))
    if title:
        flash(f"Renamed to “{title}”.")
    return redirect(url_for("course_settings", course=course))


@app.route("/<course>/settings")
def course_settings(course):
    """Per-course settings: the less-frequent actions — add a reference hierarchy,
    rebuild the outline from a reference, export a bundle, delete the course."""
    with db() as conn:
        crow = conn.execute(
            "SELECT title, primary_outline FROM courses WHERE course=?", (course,)).fetchone()
        if not crow:
            abort(404)
        references = conn.execute(
            "SELECT h.hierarchy, h.title FROM hierarchies h "
            "LEFT JOIN hierarchy_targets t ON t.course=h.course AND t.reference=h.hierarchy "
            "WHERE h.course=? AND h.editable=0 ORDER BY t.position, h.hierarchy",
            (course,)).fetchall()
        outline = crow["primary_outline"]
        if not outline:
            r = conn.execute(
                "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 "
                "ORDER BY hierarchy LIMIT 1", (course,)).fetchone()
            outline = r["hierarchy"] if r else None
        outline_empty = True
        if outline:
            outline_empty = conn.execute(
                "SELECT 1 FROM nodes WHERE course=? AND hierarchy=? LIMIT 1",
                (course, outline)).fetchone() is None
        # Per-reference levels (tag + count, shallowest-first) for the
        # "import objectives from a level" picker.
        ref_levels = {}
        for h in references:
            rnodes = conn.execute(
                "SELECT node_id, parent_id, level FROM nodes WHERE course=? AND hierarchy=?",
                (course, h["hierarchy"])).fetchall()
            ref_levels[h["hierarchy"]] = level_counts(rnodes)
    return render_template("course_settings.html", course=course, title=crow["title"],
                           references=references, outline_empty=outline_empty,
                           ref_levels=ref_levels, page_title=f"{course.upper()} settings")


@app.route("/<course>/delete", methods=["POST"])
def course_delete(course):
    """Delete a course and everything anchored to it: all its hierarchies (+ their
    nodes, coverage, attrs, targets) and its pool membership, then prune any
    objectives left with no course."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        # Null the outline pointer before dropping hierarchies (courses.primary_outline
        # -> hierarchies FK); children before parents so the deletes satisfy the FKs.
        conn.execute("UPDATE courses SET primary_outline=NULL WHERE course=?", (course,))
        for tbl in ("coverage", "node_attr", "node_duration", "nodes",
                    "hierarchy_targets", "course_objectives", "objectives", "hierarchies"):
            conn.execute(f"DELETE FROM {tbl} WHERE course=?", (course,))
        conn.execute("DELETE FROM courses WHERE course=?", (course,))
    apply_structural(course, f"Delete course {course}", drop_course=True)
    flash(f"Deleted course {course!r}.")
    return redirect(url_for("index"))


@app.route("/<course>/bundle")
def course_bundle_download(course):
    """Download the whole course as a single self-contained JSON bundle."""
    with db() as conn:
        try:
            doc = course_bundle.export_course(conn, course)
        except KeyError:
            abort(404)
    payload = json.dumps(doc, indent=2, ensure_ascii=False)
    # ISO-8601 local timestamp, made filename-safe (colons -> dashes in the time).
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{course}-course-{ts}.json"'})


@app.route("/course/import", methods=["POST"])
def course_import():
    """Recreate a course from an uploaded bundle (the inverse of the download)."""
    f = request.files.get("file")
    back = request.referrer or url_for("index")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(back)
    try:
        doc = json.loads(f.read().decode("utf-8", "replace"))
        with db() as conn:
            cid = course_bundle.import_course(conn, doc)
    except Exception as e:  # bad JSON, version, or id/slug clash (rolled back)
        flash(f"Import failed: {e}")
        return redirect(back)
    apply_structural(cid, f"Import course {cid}")
    # We land on the imported course (in the sidebar, with its data); no flash.
    return redirect(url_for("tree", course=cid))


def workspace_data(conn, course, H):
    """Node tree of hierarchy H with the raw objectives mapped onto each node, plus
    the unplaced pool. Single placement per hierarchy: an objective sits under one
    node of H or in the pool. Objectives within a node are ordered by the coverage
    edge's `position` (the per-node order); the pool by the master pool position."""
    objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"], "position": r["position"],
                        "node": None, "cpos": None}
            for r in conn.execute(
                "SELECT o.uuid, o.text, co.position FROM objectives o "
                "JOIN course_objectives co ON co.uuid=o.uuid AND co.course=? "
                "WHERE o.status='active'", (course,))}
    for r in conn.execute(
        "SELECT uuid, node_id, position FROM coverage "
        "WHERE course=? AND hierarchy=?", (course, H)):
        o = objs.get(r["uuid"])
        if o:
            o["node"] = r["node_id"]
            o["cpos"] = r["position"]

    # The bulk editor edits objectives as plan.md-style bullets, so every objective
    # carries the same abbreviated identity token the markdown editor shows (unique
    # over the course pool); editing a bullet's text while keeping its token keeps
    # the objective's identity (see node_objectives_bulk).
    tokens = plan_io.abbrev_tokens(list(objs))
    for u, o in objs.items():
        o["token"] = tokens.get(u, u[:plan_io.TOKEN_FLOOR])

    by_node = {}
    for o in objs.values():
        if o["node"]:
            by_node.setdefault(o["node"], []).append(o)
    for lst in by_node.values():
        lst.sort(key=lambda o: (o["cpos"] if o["cpos"] is not None else 1 << 30,
                                o["text"].lower()))
    pool = sorted((o for o in objs.values() if not o["node"]),
                  key=lambda o: (0, o["position"]) if o["position"] is not None
                  else (1, o["text"].lower()))

    nodes = conn.execute("SELECT * FROM nodes WHERE course=? AND hierarchy=? ORDER BY ordinal",
                         (course, H)).fetchall()
    return nodes, by_node, pool


def workspace_stats(nodes, by_node, pool):
    """Two complementary coverage directions for a hierarchy:

    - leaf coverage: of this hierarchy's leaves, how many have >=1 objective
      (what you want ~100% of for a standard like the CED -- every leaf taught);
    - placement: of all the course's raw objectives, how many are placed somewhere
      in this hierarchy (what you want ~100% of for the course outline -- every
      objective has a home).
    """
    leaves = [n for n in nodes if n["is_leaf"]]
    covered = sum(1 for n in leaves if by_node.get(n["node_id"]))
    placed = sum(len(v) for v in by_node.values())  # objectives with a home here
    total = placed + len(pool)                       # all the course's objectives
    return {"leaves": len(leaves), "covered": covered, "gaps": len(leaves) - covered,
            "pct": round(100 * covered / len(leaves)) if leaves else 0,
            "pool": len(pool), "placed": placed, "total": total,
            "placed_pct": round(100 * placed / total) if total else 0,
            "levels": level_counts(nodes)}


def level_counts(nodes):
    """Per-level node tallies for the stat bar, shallowest-first
    (e.g. [{count: 4, label: 'units'}, {count: 123, label: 'pages'}]).

    A tag maps 1:1 to a heading depth, so order the tags by the depth of any
    node carrying them."""
    parent_of = {n["node_id"]: n["parent_id"] for n in nodes}

    def depth(nid):
        d = 0
        while parent_of.get(nid):
            nid, d = parent_of[nid], d + 1
        return d

    counts, depths = {}, {}
    for n in nodes:
        tag = n["level"]
        counts[tag] = counts.get(tag, 0) + 1
        depths.setdefault(tag, depth(n["node_id"]))
    return [{"count": counts[t], "label": pluralize_tag(t, counts[t]), "tag": t}
            for t in sorted(counts, key=lambda t: depths[t])]


def pluralize_tag(tag, n):
    """A level tag as a display word, pluralized for the count: the hyphenated
    tag becomes spaced words ('learning-objective' -> 'learning objectives')."""
    words = tag.replace("-", " ")
    if n != 1:
        words += "es" if words.endswith(("s", "x", "ch", "sh")) else "s"
    return words


@app.route("/<course>")
def tree(course):
    """The course landing page is its course outline."""
    return redirect(url_for("plan", course=course))


@app.route("/<course>/h/<hierarchy>")
def hierarchy_view(course, hierarchy):
    """The unified workspace for any hierarchy: its node tree with a droppable zone
    per node + the raw-objective pool. Editable hierarchies also edit structure."""
    with db() as conn:
        h = conn.execute("SELECT * FROM hierarchies WHERE hierarchy=? AND course=?",
                         (hierarchy, course)).fetchone()
        if not h:
            # Stale/renamed hierarchy URL (e.g. an old tab after a rename): fall
            # back to the course's landing rather than hard-404, so old links and
            # post-save redirects recover instead of erroring.
            if conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
                return redirect(url_for("tree", course=course))
            abort(404, f"no course {course!r}")
        nodes, by_node, pool = workspace_data(conn, course, hierarchy)
        # node_id -> amount (the duration's number; the unit is implied by the
        # node's level -- weeks on units, days on lessons). Drives the inline
        # duration fields on the editable outline.
        durations = {r["node_id"]: (int(r["amount"]) if float(r["amount"]).is_integer()
                                    else r["amount"])
                     for r in conn.execute("SELECT node_id, amount FROM node_duration"
                                           " WHERE course=? AND hierarchy=?", (course, hierarchy))}
        # Only the editable outline shows unit week pills; compute their actual spans
        # from the bound calendar so they match the calendar view.
        unit_weeks = _outline_unit_weeks(conn, course) if h["editable"] else {}
    # The outline's stored title is the generic "Course outline"; qualify it with
    # the course (like the objectives page) so the page title isn't ambiguous.
    # Reference titles already include the course (e.g. "WIDGETS CED").
    title = f"{course.upper()} {h['title'].lower()}" if h["editable"] else h["title"]
    return render_template(
        "workspace.html", course=course, ref=hierarchy,
        page_title=title,
        editable=bool(h["editable"]), pool=pool,
        durations=durations, unit_weeks=unit_weeks,
        tree=build_tree(nodes, by_node, set()),
        stats=workspace_stats(nodes, by_node, pool))


@app.route("/<course>/h/<hierarchy>/stats")
def workspace_stats_partial(course, hierarchy):
    with db() as conn:
        nodes, by_node, pool = workspace_data(conn, course, hierarchy)
    return render_template("_wstats.html", course=course, ref=hierarchy,
                           stats=workspace_stats(nodes, by_node, pool))


@app.route("/<course>/h/<hierarchy>/place", methods=["POST"])
def place(course, hierarchy):
    """Drag a raw objective: `to` is "node-<id>" (map/recategorize) or "pool"
    (unmap). Single placement per hierarchy. `ids` carries the destination zone's
    full order so the per-node order (coverage.position) -- or the master pool
    order (course_objectives.position) when dropped in the pool -- is persisted."""
    uuid = request.form.get("uuid")
    to = (request.form.get("to") or "").strip()
    node = to[5:] if to.startswith("node-") else None
    with db() as conn:
        # A stale tab can POST to a renamed/removed hierarchy; refuse rather than
        # write orphan coverage under a slug that no longer exists.
        hrow = conn.execute("SELECT editable FROM hierarchies WHERE hierarchy=? AND course=?",
                            (hierarchy, course)).fetchone()
        if not hrow:
            abort(409, "hierarchy no longer exists -- reload the page")
        # Where the dragged objective sat BEFORE this drop, so the commit message
        # can tell a reorder from an actual move (node it was in, or None = pool).
        prev = conn.execute("SELECT node_id FROM coverage WHERE course=? AND hierarchy=? AND uuid=?",
                            (course, hierarchy, uuid)).fetchone()
        prev_node = prev["node_id"] if prev else None
        conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND uuid=?",
                     (course, hierarchy, uuid))
        if node:
            conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position) "
                         "VALUES (?, ?, ?, ?, 0)", (course, hierarchy, uuid, node))
            # Renumber the destination node by the zone's order (the dropped item
            # included). ids not in this node (defensive) simply match nothing.
            for i, u in enumerate(_id_list("ids")):
                conn.execute("UPDATE coverage SET position=? WHERE course=? AND hierarchy=? "
                             "AND node_id=? AND uuid=?", (i, course, hierarchy, node, u))
        if to == "pool":
            for i, u in enumerate(_id_list("ids")):
                conn.execute("UPDATE course_objectives SET position=? "
                             "WHERE course=? AND uuid=?", (i, course, u))
        conn.commit()
    # Commit message: name the hierarchy (editable outline reads best as "the
    # <course> outline"; references go by slug) and distinguish what actually
    # happened -- map, recategorize, unmap, or a pure reorder -- using where the
    # objective sat before (prev_node).
    where = f"the {course} outline" if hrow["editable"] else f"{course}/{hierarchy}"
    if to == "pool":
        g.action_phrase = (f"removed objectives from {where}" if prev_node
                           else f"reordered the {course} pool")
    elif node:
        g.action_phrase = (f"reordered objectives in {where}" if prev_node == node
                           else f"placed objectives in {where}")
    return ("", 204)


# An optional leading list marker on a bulk-editor line ("- " / "* "); tolerated
# so the markdown bullets the editor renders parse, as do plain unmarked lines.
_BULLET_PREFIX = re.compile(r"^\s*[-*]\s+")


def _parse_objective_lines(raw):
    """A bulk-editor buffer -> [(text, token|None)] in order, blanks dropped. Each
    line is a plan.md-style objective bullet: an optional "- " marker, the text,
    and an optional trailing "(#token)" identity token (see plan_io.TOKEN_RE)."""
    out = []
    for line in raw.splitlines():
        s = _BULLET_PREFIX.sub("", line, count=1).strip()
        tok = plan_io.TOKEN_RE.search(s)
        token = tok.group(1) if tok else None
        text = (plan_io.TOKEN_RE.sub("", s) if tok else s).strip()
        if text:
            out.append((text, token))
    return out


@app.route("/<course>/h/<hierarchy>/node/<node_id>/objectives", methods=["POST"])
def node_objectives_bulk(course, hierarchy, node_id):
    """Set a leaf node's objectives in bulk from the editor's plan.md-style bullets.

    Each line is "[- ]text [(#token)]". A line whose token resolves (by shortest-
    unique prefix) against the course pool keeps that objective's identity and
    adopts the line's text (so an existing objective can be reworded in place);
    a tokenless line interns by text (reused or minted). The resulting objectives
    are placed under this node in order (single placement per hierarchy, like a
    drag); those previously here but no longer listed go back to the pool. Returns
    JSON {items: refreshed zone HTML, doc: the re-tokenized editor buffer}."""
    parsed = _parse_objective_lines(request.form.get("objectives") or "")
    with db() as conn:
        # A stale tab can POST to a renamed/removed hierarchy; refuse rather than
        # write orphan coverage under a slug that no longer exists.
        if not conn.execute("SELECT 1 FROM hierarchies WHERE hierarchy=? AND course=?",
                            (hierarchy, course)).fetchone():
            abort(409, "hierarchy no longer exists -- reload the page")
        before = {r["uuid"] for r in conn.execute(
            "SELECT uuid FROM coverage WHERE course=? AND hierarchy=? AND node_id=?",
            (course, hierarchy, node_id))}
        # Resolve tokens against the course's existing pool (its objective registry).
        known = [r["uuid"] for r in conn.execute(
            "SELECT uuid FROM course_objectives WHERE course=?", (course,))]
        objs, seen = [], set()
        for text, token in parsed:
            uuid = plan_io.resolve_token(token, known) if token else None
            if uuid:
                # Token wins: the edited text is the source of truth -> adopt it.
                conn.execute("UPDATE objectives SET text=? WHERE uuid=?", (text, uuid))
            else:
                row = conn.execute("SELECT uuid FROM objectives WHERE course=? AND text=?",
                                   (course, text)).fetchone()
                uuid = row["uuid"] if row else str(uuidlib.uuid4())
                if not row:
                    conn.execute("INSERT INTO objectives(uuid, course, text) VALUES (?, ?, ?)",
                                 (uuid, course, text))
                    known.append(uuid)
            if uuid in seen:        # a repeated token/text collapses to one placement
                continue
            seen.add(uuid)
            conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid) VALUES (?, ?)",
                         (course, uuid))
            # Single placement per hierarchy: clear any prior home, then place here.
            conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND uuid=?",
                         (course, hierarchy, uuid))
            conn.execute("INSERT INTO coverage(course, hierarchy, uuid, node_id, position) "
                         "VALUES (?, ?, ?, ?, ?)", (course, hierarchy, uuid, node_id, len(objs)))
            objs.append({"uuid": uuid, "text": text})
        # Objectives dropped from the list go back to the pool (coverage removed).
        for u in before - seen:
            conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND node_id=? AND uuid=?",
                         (course, hierarchy, node_id, u))
        conn.commit()
        # Re-tokenize against the (now-updated) pool so the editor's buffer reflects
        # tokens minted this save -- a second save then keeps those identities.
        tokens = plan_io.abbrev_tokens([r["uuid"] for r in conn.execute(
            "SELECT uuid FROM course_objectives WHERE course=?", (course,))])
    doc = "".join(f"- {o['text']}  (#{tokens.get(o['uuid'], o['uuid'][:plan_io.TOKEN_FLOOR])})\n"
                  for o in objs)
    return jsonify(items=render_template("_rawitems.html", objectives=objs), doc=doc)


@app.route("/<course>/h/<hierarchy>/upload", methods=["POST"])
def hierarchy_upload(course, hierarchy):
    """Upload a (uuid, text, node_id) TSV placing objectives into THIS hierarchy.
    Identity is the uuid (text updated to match); each named objective's placement
    in this hierarchy is REPLACED by its node_id (previously-unplaced ones are added
    and placed). Unknown node_ids are reported and leave the prior placement be."""
    back = redirect(url_for("hierarchy_view", course=course, hierarchy=hierarchy))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return back
    try:
        rows, _mode = import_objectives.parse_coverage(
            f.read().decode("utf-8", "replace"), default_hierarchy=hierarchy)
    except ValueError as e:
        flash(f"Upload failed: {e}")
        return back
    stats, dangling = import_objectives.upsert(db_path(), course, rows)
    apply_structural(course, f"Import objectives into {course}/{hierarchy}")
    msg = _upsert_msg(f.filename, stats)
    unknown = dangling.get(hierarchy, [])
    if unknown:
        msg += (f" · {len(unknown)} id(s) not in this hierarchy (placement kept): "
                f"{', '.join(unknown[:6])}{'…' if len(unknown) > 6 else ''}")
    flash(msg)
    return back


def _upsert_msg(filename, stats):
    msg = (f"Imported {filename!r}: {stats['objectives_new']} new, "
           f"{stats['pooled']} added to the pool, {stats['placed']} placement(s)")
    if stats["text_updated"]:
        msg += f", {stats['text_updated']} text update(s)"
    if stats["text_conflicts"]:
        msg += f" · {stats['text_conflicts']} text update(s) skipped (text already in use)"
    return msg


# --------------------------------------------------------------------------
# Objectives + mapping (write views)


@app.route("/<course>/objectives")
def objectives(course):
    """A compact table of the course's objectives: a text column plus one column
    per reference hierarchy, each cell holding the (slug-colored) node ids the
    objective covers there. Headers sort -- text lexically, a hierarchy column by
    document order (node ordinal; lexical-by-id does NOT match it)."""
    BIG = 10 ** 9
    with db() as conn:
        cols = [dict(r) for r in conn.execute(
            "SELECT h.hierarchy, h.editable, h.title FROM hierarchies h "
            "LEFT JOIN hierarchy_targets t ON t.course=h.course AND t.reference=h.hierarchy "
            "WHERE h.course=? ORDER BY h.editable, t.position, h.hierarchy", (course,))]
        slugs = [h["hierarchy"] for h in cols]
        # (hierarchy, node_id) -> (display_id, sort_ord, tooltip). References use the
        # verbatim id + document ordinal; editable outlines use synthetic ids.
        node_meta = {}
        for h in cols:
            ns = conn.execute(
                "SELECT node_id, parent_id, ordinal, text FROM nodes WHERE course=? AND hierarchy=?",
                (course, h["hierarchy"])).fetchall()
            firstline = lambda t: re.sub(r"[`*]", "", (t or "").split("\n", 1)[0])
            if h["editable"]:
                los = {r["node_id"]: r["value"] for r in conn.execute(
                    "SELECT node_id, value FROM node_attr "
                    "WHERE course=? AND hierarchy=? AND name='learning_objective'",
                    (course, h["hierarchy"]))}
                sids = synthetic_ids(ns)
                for n in ns:
                    disp, seq = sids[n["node_id"]]
                    tip = (n["text"] or "").strip() or los.get(n["node_id"], "")
                    node_meta[(h["hierarchy"], n["node_id"])] = (disp, seq, firstline(tip))
            else:
                for n in ns:
                    node_meta[(h["hierarchy"], n["node_id"])] = (
                        n["node_id"], n["ordinal"], firstline(n["text"]))
        objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"],
                            "cells": {s: {"tags": [], "ord": BIG} for s in slugs}}
                for r in conn.execute(
                    "SELECT o.uuid, o.text FROM objectives o JOIN course_objectives co "
                    "ON co.uuid=o.uuid AND co.course=? WHERE o.status='active'", (course,))}
        for r in conn.execute(
            "SELECT uuid, hierarchy, node_id FROM coverage WHERE course=?", (course,)):
            o = objs.get(r["uuid"])
            if not o or r["hierarchy"] not in slugs:
                continue
            disp, ordn, title = node_meta.get((r["hierarchy"], r["node_id"]),
                                               (r["node_id"], BIG, ""))
            cell = o["cells"][r["hierarchy"]]
            cell["tags"].append({"id": disp, "title": title, "ord": ordn})
            cell["ord"] = min(cell["ord"], ordn)
        for o in objs.values():
            o["sort"] = re.sub(r"[`*]", "", o["text"]).lower()  # visible-text sort key
            for cell in o["cells"].values():
                cell["tags"].sort(key=lambda t: t["ord"])
    rows = sorted(objs.values(), key=lambda o: o["sort"])
    with db() as conn:
        other_courses = [r["course"] for r in conn.execute(
            "SELECT course FROM courses WHERE course<>? ORDER BY course", (course,))]
    return render_template("objectives.html", course=course, objectives=rows,
                           hierarchies=cols, total=len(rows),
                           other_courses=other_courses,
                           page_title=f"{course.upper()} objectives")


@app.route("/<course>/objectives.tsv")
def objectives_tsv(course):
    """Download the course's objectives as a uuid/text TSV."""
    with db() as conn:
        rows = conn.execute(
            "SELECT o.uuid, o.text FROM objectives o JOIN course_objectives co "
            "ON co.uuid=o.uuid AND co.course=? WHERE o.status='active' ORDER BY o.text",
            (course,)).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(["uuid", "text"])
    writer.writerows([(r["uuid"], r["text"]) for r in rows])
    return Response(
        buf.getvalue(), mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f'attachment; filename="{course}-objectives.tsv"'})


@app.route("/<course>/outline/edit")
def outline_edit(course):
    """Full-page Markdown editor for the course's round-trippable plan.md (the
    storage form from plan_io.render_course). Saving posts to outline_source."""
    with db() as conn:
        try:
            files, _n_obj, _n_cov = plan_io.render_course(conn, course)
        except KeyError:
            abort(404, f"no course {course!r}")
    return render_template("outline_edit.html", course=course,
                           page_title=f"{course.upper()} outline source",
                           text=files[plan_io.PLAN_FILE])


@app.route("/<course>/outline/source", methods=["GET", "POST"])
def outline_source(course):
    """GET: the editable plan.md text (the round-trippable storage form). POST:
    load the edited markdown into the db (plan_io.load_plan_text) then write the
    canonical plan.md + TSVs to the courses directory, leaving the course clean. A parse/load
    error is reported and writes nothing (the db and disk stay untouched)."""
    if request.method == "GET":
        with db() as conn:
            try:
                files, *_ = plan_io.render_course(conn, course)
            except KeyError:
                abort(404, f"no course {course!r}")
        return Response(files[plan_io.PLAN_FILE], mimetype="text/markdown")

    text = request.form.get("text", "")
    try:
        plan_io.load_plan_text(db_path(), course, text)
    except (ValueError, sqlite3.Error) as e:
        # Don't clobber: db and disk are untouched. Re-render with the user's buffer
        # intact so the failed edit isn't lost.
        flash(f"Couldn't save: {e}")
        return render_template("outline_edit.html", course=course,
                               page_title=f"{course.upper()} outline source",
                               text=text, error=str(e))
    course_dir = os.path.join(courses_root(), course)
    _path, n_obj, n_cov = plan_io.write_course(db_path(), course, course_dir)
    _record_edit(course, f"Edit {course.upper()} outline via Markdown")
    flash(f"Saved outline · {n_obj} objectives, {n_cov} coverage edges")
    # Back to the regular (non-markdown) outline workspace after a successful save.
    return redirect(url_for("plan", course=course))


@app.route("/<course>/objectives/upload", methods=["POST"])
def objectives_upload(course):
    """Upload objectives for the course. Either a plain-text list (one objective
    per line -> interned and pooled) or a full (uuid, text, hierarchy_id, node_id)
    TSV -> objectives pooled and placed, with each named (hierarchy, objective)'s
    placement REPLACED by its node_id (identity is the uuid; text updated to match).
    Unknown node_ids per hierarchy are reported."""
    back = redirect(url_for("objectives", course=course))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return back
    try:
        rows, _mode = import_objectives.parse_coverage(f.read().decode("utf-8", "replace"))
    except ValueError as e:
        flash(f"Upload failed: {e}")
        return back
    stats, dangling = import_objectives.upsert(db_path(), course, rows)
    apply_structural(course, f"Import objectives into {course}")
    msg = _upsert_msg(f.filename, stats)
    if dangling:
        n = sum(len(v) for v in dangling.values())
        msg += f" · {n} unknown node id(s) across {len(dangling)} hierarchy(ies)"
    flash(msg)
    return back


@app.route("/<course>/objectives/import-from", methods=["POST"])
def objectives_import_from(course):
    """Copy another course's pool objectives into this one as new, independent
    objectives (re-interned per-course -- see import_objectives.copy_objectives)."""
    src = (request.form.get("source") or "").strip()
    back = redirect(url_for("objectives", course=course))
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        ok_src = src and src != course and conn.execute(
            "SELECT 1 FROM courses WHERE course=?", (src,)).fetchone()
    if not ok_src:
        flash("Pick a different existing course to import objectives from.")
        return back
    n = import_objectives.copy_objectives(db_path(), src, course)
    apply_structural(course, f"Import objectives from {src} into {course}")
    flash(f"Imported {n} objective(s) from {src.upper()}.")
    return back


def _back(course):
    """Redirect to the posting page, re-anchored to a node if `anchor` was sent.

    Preserves the referrer's query string (e.g. ?filter=gaps) and re-attaches the
    `#anchor` fragment so saving from the long outline keeps your scroll position.
    """
    target = request.referrer or url_for("objectives", course=course)
    anchor = (request.form.get("anchor") or "").strip()
    if anchor:
        target = target.split("#", 1)[0] + "#" + anchor
    return redirect(target)


def _outline_unit_weeks(conn, course):
    """Map each outline unit's node_id -> {weeks_shown, derived}, by laying the outline
    on the course's bound calendar (calendar_view.build_calendar). Lets the outline's
    week pills show the same actual span as the calendar -- including auto-sized units
    and the greedy last unit, flagged `derived`. Empty when no calendar is bound or it
    won't load (the pills then fall back to a plain placeholder)."""
    crow = conn.execute("SELECT calendar FROM courses WHERE course=?", (course,)).fetchone()
    cal_id = crow["calendar"] if crow else None
    if not cal_id:
        return {}
    try:
        bs, data = calendar_view.load_calendar(cal_id, CALENDAR_DIR)
    except (OSError, ValueError):
        return {}
    view = calendar_view.build_calendar(bs, data, _outline_units(conn, course))
    return {u["node_id"]: {"weeks_shown": u["weeks_shown"], "derived": u["derived"]}
            for u in view["units"]
            if not u.get("break_section") and u.get("node_id")}


def _outline_ctx(conn, course):
    """Render context for the editable outline units partial (_outline_units.html):
    the unit/lesson tree plus the inline durations. Shared by the structural-edit
    routes so each can return just that region for an htmx in-place swap (no full
    reload, scroll preserved)."""
    O = ensure_outline(conn, course)
    nodes, by_node, _pool = workspace_data(conn, course, O)
    durations = {r["node_id"]: (int(r["amount"]) if float(r["amount"]).is_integer()
                                else r["amount"])
                 for r in conn.execute("SELECT node_id, amount FROM node_duration"
                                       " WHERE course=? AND hierarchy=?", (course, O))}
    return dict(course=course, ref=O, editable=True, durations=durations,
                unit_weeks=_outline_unit_weeks(conn, course),
                tree=build_tree(nodes, by_node, set()))


def _outline_swap(conn, course, flash_msg=None):
    """An htmx response that swaps the freshly re-rendered outline units region into
    #outline-units. An optional flash rides along as an `HX-Trigger` event since
    there's no page reload to surface a server flash."""
    resp = make_response(render_template("_outline_units.html",
                                         **_outline_ctx(conn, course)))
    if flash_msg:
        resp.headers["HX-Trigger"] = json.dumps({"flash": flash_msg})
    return resp


def active_ref():
    """The hierarchy this request targets: its explicit `ref` arg/field, else None.
    The workspace's forms send the hierarchy being viewed; there is no default --
    a request that doesn't name one simply doesn't place coverage."""
    return (request.values.get("ref") or "").strip() or None


@app.route("/<course>/objective/new", methods=["POST"])
def objective_new(course):
    text = (request.form.get("text") or "").strip()
    node = (request.form.get("node_id") or "").strip()
    u = None
    if text:
        with db() as conn:
            R = active_ref()
            # Intern by text: reuse the existing objective, or create a new one.
            row = conn.execute("SELECT uuid FROM objectives WHERE course=? AND text=?",
                               (course, text)).fetchone()
            u = row[0] if row else str(uuidlib.uuid4())
            if not row:
                conn.execute("INSERT INTO objectives(uuid, course, text) VALUES (?, ?, ?)",
                             (u, course, text))
            conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid) VALUES (?, ?)",
                         (course, u))
            if node and R:
                nxt = conn.execute("SELECT COALESCE(MAX(position), -1)+1 FROM coverage "
                                   "WHERE course=? AND hierarchy=? AND node_id=?",
                                   (course, R, node)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position) "
                             "VALUES (?, ?, ?, ?, ?)", (course, R, u, node, nxt))
            conn.commit()
    # Workspace (htmx): return just the new raw item to drop into the target zone.
    if request.form.get("as") == "item":
        if not text:
            return ("", 204)
        return render_template("_rawitem.html", o={"uuid": u, "text": text})
    # The new objective appears in the pool/table on reload; no flash.
    return _back(course)


@app.route("/<course>/objective/<uuid>/edit", methods=["POST"])
def objective_edit(course, uuid):
    text = (request.form.get("text") or "").strip()
    if text:
        with db() as conn:
            # Text is the natural key: refuse an edit that would duplicate another
            # objective (the user should map both to a node, not retype the text).
            clash = conn.execute("SELECT 1 FROM objectives WHERE course=? AND text=? AND uuid<>?",
                                 (course, text, uuid)).fetchone()
            if clash:
                if request.headers.get("HX-Request"):
                    return ("an objective with that text already exists", 409)
                flash("Not saved: an objective with that text already exists.")
                return _back(course)
            conn.execute("UPDATE objectives SET text = ? WHERE uuid = ?", (text, uuid))
            conn.commit()
    # An edit doesn't change structure/coverage, so no re-render -- just persist.
    # (Autosave is htmx -> 204; the edited text is already on screen, so no flash.)
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


# --------------------------------------------------------------------------
# Lesson builder: synthesize raw -> lesson objectives, then schedule into lessons


def _id_list(field="ids"):
    """Read an id list sent either as repeated fields or one comma-joined field."""
    vals = request.form.getlist(field)
    if len(vals) == 1 and "," in vals[0]:
        vals = vals[0].split(",")
    return [v for v in (s.strip() for s in vals) if v]


# --------------------------------------------------------------------------
# The Plan page: Units -> Lessons, with raw objectives placed into them.


@app.route("/<course>/plan")
def plan(course):
    """Back-compat: the plan is now the outline hierarchy's workspace."""
    with db() as conn:
        # Don't conjure a course from an unknown slug -- this GET is what browser
        # probes (/apple-touch-icon.png -> /<course> -> /<course>/plan) land on.
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404, f"no course {course!r}")
        O = outline_hierarchy(conn, course) or ensure_outline(conn, course)
        conn.commit()
    return redirect(url_for("hierarchy_view", course=course, hierarchy=O))


def _outline_units(conn, course):
    """The course outline as ordered units for the calendar: each
    {title, weeks (float|None), pin ({edge, week}|None), lessons: [{node_id, title,
    days (int)}]}. Lessons not under a unit are omitted (the calendar lays out units)."""
    O = outline_hierarchy(conn, course)
    if not O:
        return []
    durs = {nid: (amt, unit) for nid, amt, unit in conn.execute(
        "SELECT node_id, amount, unit FROM node_duration WHERE course=? AND hierarchy=?",
        (course, O))}
    pins = {nid: {"edge": edge, "week": week} for nid, week, edge in conn.execute(
        "SELECT node_id, week, edge FROM node_pin WHERE course=? AND hierarchy=?",
        (course, O))}
    nodes = conn.execute("SELECT node_id, parent_id, level, text FROM nodes "
                         "WHERE course=? AND hierarchy=? ORDER BY ordinal", (course, O)).fetchall()
    lessons_by_unit = {}
    for n in nodes:
        if n["level"] == "lesson":
            lessons_by_unit.setdefault(n["parent_id"], []).append(n)
    units = []
    for n in nodes:
        if n["level"] != "unit":
            continue
        d = durs.get(n["node_id"])
        weeks = d[0] if (d and d[1] == "week") else None
        lessons = []
        for L in lessons_by_unit.get(n["node_id"], []):
            ld = durs.get(L["node_id"])
            days = int(ld[0]) if (ld and ld[1] == "day") else 1
            lessons.append({"node_id": L["node_id"],
                            "title": L["text"] or "Untitled lesson", "days": days})
        units.append({"node_id": n["node_id"], "title": n["text"] or "Untitled unit",
                      "weeks": weeks, "pin": pins.get(n["node_id"]), "lessons": lessons})
    return units


def _calendar_ctx(conn, course):
    """Template context for the calendar view: {calendar_id, view, error, stats}.
    view is the laid-out calendar (calendar_view.build_calendar) or None when no
    calendar is bound / it can't load; stats is the outline's count subheader (units
    · lessons · placed objectives), reused from the workspace."""
    O = outline_hierarchy(conn, course)
    stats = None
    if O:
        nodes, by_node, pool = workspace_data(conn, course, O)
        stats = workspace_stats(nodes, by_node, pool)
    crow = conn.execute("SELECT calendar FROM courses WHERE course=?", (course,)).fetchone()
    cal_id = crow["calendar"] if crow else None
    if not cal_id:
        return {"calendar_id": None, "view": None, "error": None, "stats": stats}
    try:
        bs, data = calendar_view.load_calendar(cal_id, CALENDAR_DIR)
    except (OSError, ValueError) as e:
        return {"calendar_id": cal_id, "view": None,
                "error": f"Couldn't load calendar {cal_id!r}: {e}", "stats": stats}
    view = calendar_view.build_calendar(bs, data, _outline_units(conn, course))
    return {"calendar_id": cal_id, "view": view, "error": None, "stats": stats}


@app.route("/<course>/calendar")
def calendar(course):
    """A calendar view of how the course outline lays out across the school year."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        ctx = _calendar_ctx(conn, course)
    return render_template("calendar.html", course=course,
                           page_title=f"{course.upper()} calendar", **ctx)


@app.route("/<course>/outline/import", methods=["POST"])
def outline_import(course):
    """Build the course outline from a reference hierarchy: its first two levels
    become units and lessons, and each objective is placed into the lesson whose
    reference subtree covers it. Replaces the existing outline (see
    plan_io.import_structure)."""
    reference = (request.form.get("reference") or "").strip()
    with db() as conn:
        if not conn.execute(
                "SELECT 1 FROM hierarchies WHERE hierarchy=? AND course=? AND editable=0",
                (reference, course)).fetchone():
            abort(404, f"no reference {reference!r} for course {course!r}")
        O = ensure_outline(conn, course)
        nu, nl, npl = plan_io.import_structure(conn, course, O, reference)
        conn.commit()
    flash(f"Built the outline from {reference}: {nu} unit(s), {nl} lesson(s), "
          f"{npl} objective placement(s).")
    return redirect(url_for("plan", course=course))


@app.route("/<course>/objectives/import-level", methods=["POST"])
def objectives_import_level(course):
    """Create an interned objective from every node at a chosen level of a reference
    hierarchy, placing each onto its source node (see import_objectives.import_level).
    Idempotent -- re-running reuses existing objectives, adds no duplicates."""
    hierarchy = (request.form.get("hierarchy") or "").strip()
    level = (request.form.get("level") or "").strip()
    with db() as conn:
        if not conn.execute(
                "SELECT 1 FROM hierarchies WHERE hierarchy=? AND course=? AND editable=0",
                (hierarchy, course)).fetchone():
            abort(404, f"no reference {hierarchy!r} for course {course!r}")
        has_level = conn.execute(
            "SELECT 1 FROM nodes WHERE course=? AND hierarchy=? AND level=? LIMIT 1",
            (course, hierarchy, level)).fetchone()
    if not has_level:
        flash("Pick a hierarchy level that has nodes.")
        return redirect(url_for("course_settings", course=course))
    stats = import_objectives.import_level(db_path(), course, hierarchy, level)
    apply_structural(course, f"Import {course}/{hierarchy} '{level}' nodes as objectives")
    flash(f"Imported the '{level}' level of {hierarchy}: "
          f"{stats['objectives_new']} new objective(s), "
          f"{stats['pooled']} added to the pool, {stats['placed']} placed.")
    return redirect(url_for("objectives", course=course))


# --- Units ---

@app.route("/<course>/unit/new", methods=["POST"])
def unit_new(course):
    # Added via the "+" by the page title (appends), or an insert-here "+" between
    # units (form sends `before`=<unit id>). No title yet -- the new unit's title
    # input is focused on reload for immediate editing -- though one may be sent.
    title = (request.form.get("title") or "").strip()
    before = (request.form.get("before") or "").strip() or None
    with db() as conn:
        O = ensure_outline(conn, course)
        pos = None
        if before:
            row = conn.execute("SELECT ordinal FROM nodes WHERE course=? AND hierarchy=? "
                               "AND node_id=? AND level='unit'", (course, O, before)).fetchone()
            if row is not None:
                pos = row["ordinal"]
                # Open a gap: shift this unit and everything after it down by one.
                conn.execute("UPDATE nodes SET ordinal=ordinal+1 WHERE course=? AND hierarchy=? "
                             "AND level='unit' AND ordinal>=?", (course, O, pos))
        if pos is None:   # append (the page-title "+", or a stale `before`)
            pos = conn.execute("SELECT COALESCE(MAX(ordinal), -1)+1 FROM nodes "
                               "WHERE course=? AND hierarchy=? AND level='unit'", (course, O)).fetchone()[0]
        conn.execute("INSERT INTO nodes(course, hierarchy, node_id, parent_id, level, is_leaf,"
                     " ordinal, text) VALUES (?, ?, ?, NULL, 'unit', 0, ?, ?)",
                     (course, O, str(uuidlib.uuid4()), pos, title))
        conn.commit()
        # From the calendar's append-unit affordance: re-lay-out and swap #cal-content
        # (a new unit has no lesson to focus, so just re-render).
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
        if request.headers.get("HX-Request"):
            return _outline_swap(conn, course)
    return redirect(url_for("hierarchy_view", course=course, hierarchy=O, focus_new_unit=1))


@app.route("/<course>/unit/<unit_id>/rename", methods=["POST"])
def unit_rename(course, unit_id):
    title = (request.form.get("title") or "").strip()
    if title:
        with db() as conn:
            O = outline_hierarchy(conn, course)
            conn.execute("UPDATE nodes SET text=? WHERE course=? AND hierarchy=? AND node_id=? "
                         "AND level='unit'", (title, course, O, unit_id))
            conn.commit()
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/unit/<unit_id>/delete", methods=["POST"])
def unit_delete(course, unit_id):
    with db() as conn:
        O = outline_hierarchy(conn, course)
        # Unassign its lessons; return its rough raws to the pool; drop the unit.
        conn.execute("UPDATE nodes SET parent_id=NULL WHERE course=? AND hierarchy=? AND parent_id=?",
                     (course, O, unit_id))
        conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND node_id=?", (course, O, unit_id))
        conn.execute("DELETE FROM node_attr WHERE course=? AND hierarchy=? AND node_id=?", (course, O, unit_id))
        conn.execute("DELETE FROM node_duration WHERE course=? AND hierarchy=? AND node_id=?", (course, O, unit_id))
        conn.execute("DELETE FROM nodes WHERE course=? AND hierarchy=? AND node_id=?", (course, O, unit_id))
        conn.commit()
        msg = "Deleted unit; lessons moved to Unassigned, rough raws back in the pool."
        # From the calendar: re-lay-out and swap #cal-content (the deleted unit's
        # lessons become unassigned, so they drop off the calendar).
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
        if request.headers.get("HX-Request"):
            return _outline_swap(conn, course, msg)
    flash(msg)
    return _back(course)


@app.route("/<course>/unit/arrange", methods=["POST"])
def unit_arrange(course):
    """Drag-reorder units. Form: `ids` (unit node_ids in their new order). Persist
    the new ordinals. From the outline (DOM already reordered) return 204; from the
    calendar (`view=calendar`) the layout changes, so re-render #cal-content."""
    ids = _id_list("ids")
    with db() as conn:
        O = outline_hierarchy(conn, course)
        for pos, uid in enumerate(ids):
            conn.execute("UPDATE nodes SET ordinal=? WHERE course=? AND hierarchy=? "
                         "AND node_id=? AND level='unit'", (pos, course, O, uid))
        conn.commit()
        g.action_phrase = f"reordered units in {course}"
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
    return ("", 204)


# --- Lessons ---

def _lesson_part(key, disp, raw):
    """One lesson-plan part for the view/edit templates: its raw markdown plus the
    rendered HTML (empty when there's no content)."""
    raw = raw or ""
    return {"key": key, "disp": disp, "raw": raw,
            "html": render_markdown(raw) if raw.strip() else ""}


def _lesson_or_redirect(conn, course, lesson_id):
    """The outline + lesson row, or a Response (outline redirect / 404) to return.
    Returns (outline, row) on success, or (None, response)."""
    O = outline_hierarchy(conn, course)
    L = conn.execute(
        "SELECT node_id, parent_id, text FROM nodes WHERE course=? AND hierarchy=?"
        " AND node_id=? AND level='lesson'", (course, O, lesson_id)).fetchone() if O else None
    if L:
        return O, L
    # Stale/renamed lesson link: fall back to the outline rather than 404 (matches
    # hierarchy_view), or hard-404 if the course is gone too.
    if conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
        return None, redirect(url_for("plan", course=course))
    abort(404, f"no course {course!r}")


@app.route("/<course>/lesson/<lesson_id>")
def lesson_view(course, lesson_id):
    """The lesson plan: its free-text parts (Preview, Learning objective, …) rendered
    from the lesson's node_attr, plus the raw objectives placed in it (the plan is a
    distillation of those). Editors get per-part click-to-edit + an Edit-as-Markdown
    button; viewers see only the non-empty parts."""
    with db() as conn:
        O, L = _lesson_or_redirect(conn, course, lesson_id)
        if O is None:
            return L
        unit = None
        if L["parent_id"]:
            row = conn.execute("SELECT text FROM nodes WHERE course=? AND hierarchy=?"
                               " AND node_id=?", (course, O, L["parent_id"])).fetchone()
            unit = row["text"] if row else None
        attrs = {r["name"]: r["value"] for r in conn.execute(
            "SELECT name, value FROM node_attr WHERE course=? AND hierarchy=? AND node_id=?",
            (course, O, lesson_id))}
        dur = conn.execute("SELECT amount, unit FROM node_duration WHERE course=?"
                           " AND hierarchy=? AND node_id=?", (course, O, lesson_id)).fetchone()
        objectives = [r["text"] for r in conn.execute(
            "SELECT o.text FROM coverage cv JOIN objectives o ON o.uuid=cv.uuid"
            " WHERE cv.course=? AND cv.hierarchy=? AND cv.node_id=? ORDER BY cv.position, o.text",
            (course, O, lesson_id))]
    # All parts in canonical order (raw + rendered). The template shows every
    # part to an editor (empty ones offer "+ add"); a viewer sees only non-empty ones.
    parts = [_lesson_part(key, disp, attrs.get(key)) for key, disp in plan_io.LESSON_PARTS]
    duration = None
    if dur:
        amount = int(dur["amount"]) if float(dur["amount"]).is_integer() else dur["amount"]
        duration = f"{amount} {dur['unit']}{'' if amount == 1 else 's'}"
    return render_template("lesson.html", course=course, page_title=L["text"],
                           lesson_id=lesson_id, unit=unit, duration=duration,
                           parts=parts, has_content=any(p["html"] for p in parts),
                           objectives=objectives,
                           embed=bool(request.args.get("embed")))


@app.route("/<course>/lesson/<lesson_id>/part/<part>", methods=["POST"])
def lesson_part_save(course, lesson_id, part):
    """Save one lesson-plan part's markdown (per-part inline editor). Updates the
    part's node_attr (deletes it when emptied) and returns the re-rendered part
    fragment for htmx to swap back into the page. Autosave persists the lesson file."""
    disp = dict(plan_io.LESSON_PARTS).get(part)
    if disp is None:
        abort(404, f"no lesson part {part!r}")
    value = (request.form.get("value") or "").strip()
    with db() as conn:
        O, L = _lesson_or_redirect(conn, course, lesson_id)
        if O is None:
            return L
        if value:
            conn.execute(
                "INSERT INTO node_attr(course, hierarchy, node_id, name, value)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(course, hierarchy, node_id, name)"
                " DO UPDATE SET value=excluded.value", (course, O, lesson_id, part, value))
        else:
            conn.execute("DELETE FROM node_attr WHERE course=? AND hierarchy=? AND node_id=?"
                         " AND name=?", (course, O, lesson_id, part))
        conn.commit()
    return render_template("_lessonpart.html", course=course, lesson_id=lesson_id,
                           part=_lesson_part(part, disp, value), can_edit=True)


def _lesson_body_text(attrs):
    """The lesson body markdown for the whole-file editor: every part heading (even
    empty ones, as scaffolding) followed by its content."""
    out = []
    for key, disp in plan_io.LESSON_PARTS:
        out += [f"## {disp}", ""]
        val = (attrs.get(key) or "").strip()
        if val:
            out += [val, ""]
    return "\n".join(out).rstrip() + "\n"


@app.route("/<course>/lesson/<lesson_id>/edit")
def lesson_edit_md(course, lesson_id):
    """Full-page Markdown editor for the whole lesson file's body (all part
    sections, scaffolded). Saving posts to lesson_source."""
    with db() as conn:
        O, L = _lesson_or_redirect(conn, course, lesson_id)
        if O is None:
            return L
        attrs = {r["name"]: r["value"] for r in conn.execute(
            "SELECT name, value FROM node_attr WHERE course=? AND hierarchy=? AND node_id=?",
            (course, O, lesson_id))}
    return render_template("lesson_edit.html", course=course, lesson_id=lesson_id,
                           page_title=L["text"], text=_lesson_body_text(attrs),
                           embed=bool(request.args.get("embed")))


@app.route("/<course>/lesson/<lesson_id>/source", methods=["POST"])
def lesson_source(course, lesson_id):
    """Save the whole-lesson Markdown editor: parse the body's `## part` sections
    into node_attr (each part set or cleared), write the course (so the
    lesson file is updated), and commit. Back to the lesson view on success."""
    text = request.form.get("text", "")
    parts = plan_io._parse_lesson_body(text)
    with db() as conn:
        O, L = _lesson_or_redirect(conn, course, lesson_id)
        if O is None:
            return L
        for key, _disp in plan_io.LESSON_PARTS:
            val = (parts.get(key) or "").strip()
            if val:
                conn.execute(
                    "INSERT INTO node_attr(course, hierarchy, node_id, name, value)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(course, hierarchy, node_id, name)"
                    " DO UPDATE SET value=excluded.value", (course, O, lesson_id, key, val))
            else:
                conn.execute("DELETE FROM node_attr WHERE course=? AND hierarchy=?"
                             " AND node_id=? AND name=?", (course, O, lesson_id, key))
        conn.commit()
    plan_io.write_course(db_path(), course, os.path.join(courses_root(), course))
    _record_edit(course, f"Edit a {course.upper()} lesson plan")
    flash("Saved lesson plan")
    return redirect(url_for("lesson_view", course=course, lesson_id=lesson_id,
                            **({"embed": 1} if request.args.get("embed") else {})))


@app.route("/<course>/lesson/new", methods=["POST"])
def lesson_new(course):
    title = (request.form.get("title") or "").strip()
    unit = (request.form.get("unit") or "").strip() or None
    with db() as conn:
        O = ensure_outline(conn, course)
        # Only attach to a unit that actually exists in this outline; otherwise
        # leave the lesson unassigned rather than orphaning it under a bad id.
        if unit and not conn.execute(
                "SELECT 1 FROM nodes WHERE course=? AND hierarchy=? AND node_id=? AND level='unit'",
                (course, O, unit)).fetchone():
            unit = None
        nxt = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1)+1 FROM nodes "
            "WHERE course=? AND hierarchy=? AND level='lesson' AND parent_id IS ?",
            (course, O, unit)).fetchone()[0]
        new_id = str(uuidlib.uuid4())
        conn.execute(
            "INSERT INTO nodes(course, hierarchy, node_id, parent_id, level, is_leaf, ordinal, text) "
            "VALUES (?, ?, ?, ?, 'lesson', 1, ?, ?)",
            (course, O, new_id, unit, nxt, title))
        conn.commit()
        # From the calendar (clicking an empty day): re-lay-out and tell the view to
        # open the new lesson with its title focused.
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   focus_lesson=new_id, **_calendar_ctx(conn, course))
        if request.headers.get("HX-Request"):
            return _outline_swap(conn, course)
    # The new lesson box appears on reload; no flash.
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/edit", methods=["POST"])
def lesson_edit(course, lesson_id):
    """Edit a lesson's title (the outline/calendar inline title field). The learning
    objective is a lesson-plan part now -- edited on the lesson view, not here."""
    with db() as conn:
        O = outline_hierarchy(conn, course)
        if "title" in request.form:
            conn.execute("UPDATE nodes SET text=? WHERE course=? AND hierarchy=? AND node_id=? "
                         "AND level='lesson'",
                         ((request.form.get("title") or "").strip(), course, O, lesson_id))
        conn.commit()
        # From the calendar's lesson box: a lesson can span several week-boxes that
        # share this node_id, so re-render the calendar content for htmx to swap in
        # rather than leave the other boxes showing the stale title.
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/delete", methods=["POST"])
def lesson_delete(course, lesson_id):
    with db() as conn:
        O = outline_hierarchy(conn, course)
        # Return its raws to the pool, then drop the lesson.
        conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND node_id=?", (course, O, lesson_id))
        conn.execute("DELETE FROM node_attr WHERE course=? AND hierarchy=? AND node_id=?", (course, O, lesson_id))
        conn.execute("DELETE FROM node_duration WHERE course=? AND hierarchy=? AND node_id=?", (course, O, lesson_id))
        conn.execute("DELETE FROM nodes WHERE course=? AND hierarchy=? AND node_id=?", (course, O, lesson_id))
        conn.commit()
        msg = "Deleted lesson; its raws returned to the pool."
        # From the calendar: re-lay-out and swap #cal-content.
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
        if request.headers.get("HX-Request"):
            return _outline_swap(conn, course, msg)
    flash(msg)
    return _back(course)


@app.route("/<course>/lesson/arrange", methods=["POST"])
def lesson_arrange(course):
    """Drag lessons between units / reorder. Form: `unit` (uuid or ""/"none") + `ids`."""
    unit = (request.form.get("unit") or "").strip()
    unit_id = None if unit in ("", "none") else unit
    ids = _id_list("ids")
    with db() as conn:
        O = outline_hierarchy(conn, course)
        # Parents before the drop, to tell a reorder from a cross-unit move.
        prev = {r["node_id"]: r["parent_id"] for r in conn.execute(
            "SELECT node_id, parent_id FROM nodes WHERE course=? AND hierarchy=? AND level='lesson'",
            (course, O))}
        for pos, lid in enumerate(ids):
            conn.execute("UPDATE nodes SET parent_id=?, ordinal=? WHERE course=? AND hierarchy=? "
                         "AND node_id=? AND level='lesson'", (unit_id, pos, course, O, lid))
        conn.commit()
        moved = any(prev.get(lid) != unit_id for lid in ids)
        g.action_phrase = (f"moved lessons in {course}" if moved
                           else f"reordered lessons in {course}")
        # From the calendar's drag-and-drop: re-lay-out and swap #cal-content so the
        # moved lesson lands in its new unit/position (calendar drops only ever target
        # real units, so `unit_id` is always set here).
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
    return ("", 204)


@app.route("/<course>/node/<node_id>/duration", methods=["POST"])
def node_duration_set(course, node_id):
    """Set (or clear) an outline node's duration from the inline field. The unit is
    implied by the node's level: weeks on a unit, days on a lesson. An empty or
    negative amount clears it (back to auto-sizing); for a lesson, 1 day is the
    default and also clears the row (so plan.md stays quiet). An explicit 0 is KEPT
    -- a 0-day lesson or a 0-week unit is omitted from the calendar (auto-sized
    units otherwise get at least 1 week)."""
    raw = (request.form.get("amount") or "").strip()
    with db() as conn:
        O = outline_hierarchy(conn, course)
        row = conn.execute("SELECT level FROM nodes WHERE course=? AND hierarchy=? AND node_id=?",
                           (course, O, node_id)).fetchone()
        if not row:
            abort(404)
        unit = "week" if row["level"] == "unit" else "day"
        try:
            amount = float(raw) if raw else None
        except ValueError:
            amount = None
        clear = (amount is None or amount < 0
                 or (unit == "day" and amount == 1))
        if clear:
            conn.execute("DELETE FROM node_duration WHERE course=? AND hierarchy=? AND node_id=?",
                         (course, O, node_id))
        else:
            conn.execute("INSERT INTO node_duration(course, hierarchy, node_id, amount, unit)"
                         " VALUES (?, ?, ?, ?, ?) ON CONFLICT(course, hierarchy, node_id)"
                         " DO UPDATE SET amount=excluded.amount, unit=excluded.unit",
                         (course, O, node_id, amount, unit))
        conn.commit()
        g.action_phrase = (f"cleared a duration in {course}" if clear
                           else f"set a duration in {course}")
        # From the calendar's weeks pill: return the re-laid-out calendar content
        # so htmx swaps it in place (no full reload, scroll preserved).
        if request.form.get("view") == "calendar":
            return render_template("_calendar_content.html", course=course,
                                   **_calendar_ctx(conn, course))
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


if collab.enabled():
    # Multi-user git-backed mode: no single global db. Each user's db is built
    # lazily from their worktree; bring up the clone, push worker, and main view.
    collab.startup()
else:
    if DEMO_MODE:
        print(f"demo mode: edits autosave + commit to a throwaway git repo at "
              f"{COURSES_ROOT} (not your original courses directory)", file=sys.stderr)
    # The db is a disposable cache; the courses directory is the source of truth
    # (CLAUDE.md). So rebuild the db from disk on EVERY boot -- delete it, re-apply
    # schema.sql, reload every course (rebuild_db.rebuild). This guarantees a
    # restart picks up whatever is currently on disk -- external edits, a git pull,
    # a freshly-mounted courses directory -- rather than serving a stale cache (the
    # old create-if-absent `seed` skipped courses already in the db). Edits the
    # running app made are autosaved to the courses directory (debounced), so a
    # normal restart loses nothing; anything not yet flushed is intentionally
    # discarded in favor of what's on disk. Never fatal: a broken rebuild still
    # leaves a valid (empty) db so the app boots.
    if os.path.isdir(COURSES_ROOT):
        try:
            rebuild_db.rebuild(DB_PATH, SCHEMA_PATH, COURSES_ROOT)
            # Baseline for the external-change guard: the db now reflects the
            # courses repo at its current HEAD.
            collab.remember_head(COURSES_ROOT)
        except Exception as e:  # a broken courses directory must not stop the app booting
            print(f"rebuild: failed ({e}); falling back to an empty db", file=sys.stderr)
            ensure_schema()
    else:
        ensure_schema()


if __name__ == "__main__":
    # Local dev only: Flask's built-in (Werkzeug) server with the auto-reloader.
    # Production serves the WSGI app object (app:app) under gunicorn -- see the
    # Dockerfile and plans/production-wsgi-server.md -- so this block does not run
    # there. The module-level startup above (collab.startup() / seed) runs on
    # import either way.
    #
    # In a yolo container the app must bind 0.0.0.0 to be reachable from the host
    # browser (127.0.0.1 inside the container isn't); on a normal machine keep the
    # safer localhost default. yolo marks the container with YOLO_SESSION set to a
    # non-empty value ('cwd'/'worktree'/'1' across versions), so treat any value as
    # a yolo session. An explicit HOST env var always wins.
    default_host = "0.0.0.0" if os.environ.get("YOLO_SESSION") else "127.0.0.1"
    # Debug (with the auto-reloader) on by default for local dev; turn it OFF in
    # production (FLASK_DEBUG=0). The reloader forks a second process, which in
    # collab mode would double-run collab.startup() (a second clone + push worker
    # + refresh timer), so production MUST keep it off.
    debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    app.run(debug=debug, host=os.environ.get("HOST", default_host),
            port=int(os.environ.get("PORT", "5001")))
