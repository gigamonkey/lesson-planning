"""Git-backed multi-user collaboration layer (see plans/git-collaboration.md).

**Off by default.** Enabled only when a config file is present
(``LESSON_COLLAB_CONFIG``, default ``collab.json`` beside this file). When off,
``app.py`` runs exactly as the local single-user tool it has always been -- none
of this module's machinery is touched.

When on, the app becomes a small multi-user deployment backed entirely by git:

  * teachers sign in with **GitHub OAuth**, checked against a **static
    allowlist** that tags each handle ``editor`` or ``viewer``;
  * each **editor** gets a private sandbox -- their own SQLite db cache plus a
    git **worktree** on a branch named for their handle;
  * **viewers** share a single read-only view of ``origin/main``;
  * every save commits to the editor's branch (author = the teacher) and the
    branch is pushed to GitHub; merging happens via normal GitHub PRs;
  * ``origin/main`` is **merged** (never rebased) back into worktree branches on
    sync, so sandboxes pick up merged work without rewriting published history.

This module is **Flask-free**: it owns config, the on-disk git layout, per-user
bindings, the per-user action buffer that composes commit messages, the
background push queue, and the GitHub OAuth token/user exchange. ``app.py``
supplies the routes and request wiring on top.

On-disk layout under ``data_dir`` (a fly volume in production):

    <data_dir>/courses/            # the primary clone; kept on origin/main --
                                   #   this IS the read-only view viewers see
    <data_dir>/worktrees/<handle>/ # one git worktree per editor, branch <handle>
    <data_dir>/db/_main.sqlite     # the viewers' shared cache (built from courses/)
    <data_dir>/db/<handle>.sqlite  # one db cache per editor (built from their worktree)
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import seed as seed_module

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# The viewers' shared sandbox uses this reserved handle; it can never collide
# with a real GitHub login (logins can't contain '_' at the start in practice,
# and we sanitize, but the leading underscore keeps it clearly internal).
MAIN = "_main"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _config_path():
    return os.environ.get("LESSON_COLLAB_CONFIG",
                          os.path.join(REPO_ROOT, "collab.json"))


def _load_config():
    """Read collab.json if present, layering a few env overrides on top.

    Non-secret settings (repo, allowlist, data_dir, dev_login) live in the file;
    secrets (the OAuth client secret) come from the environment so they never
    have to be committed. Returns None when collaboration is disabled (no file).
    """
    path = _config_path()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("data_dir", os.environ.get("LESSON_DATA_DIR", "/data"))
    cfg.setdefault("repo", os.environ.get("LESSON_COURSES_REPO"))
    cfg.setdefault("main_refresh_seconds", 300)
    # Debounce window for autosave: after this many seconds of no edits, the
    # editor's dirty courses are written to their worktree and committed+pushed.
    cfg.setdefault("autosave_seconds", 2)
    cfg.setdefault("allowlist", {})
    # Normalize allowlist handles (case-insensitive match against GitHub login).
    cfg["allowlist"] = {k.lower(): v for k, v in cfg["allowlist"].items()}
    oauth = cfg.setdefault("github_oauth", {})
    oauth.setdefault("client_id", os.environ.get("GITHUB_CLIENT_ID"))
    oauth.setdefault("client_secret", os.environ.get("GITHUB_CLIENT_SECRET"))
    cfg.setdefault("dev_login", bool(os.environ.get("LESSON_DEV_LOGIN")))
    if not cfg.get("repo"):
        print("collab: config present but no `repo` set (config or "
              "LESSON_COURSES_REPO); collaboration disabled.", file=sys.stderr)
        return None
    return cfg


CONFIG = _load_config()


def enabled():
    return CONFIG is not None


def data_dir():
    return CONFIG["data_dir"]


def role_of(handle):
    """'editor' | 'viewer' | None (not on the allowlist)."""
    return CONFIG["allowlist"].get((handle or "").lower())


def dev_login_enabled():
    return enabled() and CONFIG.get("dev_login")


def _safe_handle(handle):
    """A filesystem/branch-safe form of a GitHub handle. GitHub logins are
    already [A-Za-z0-9-], but never trust input that names a path or a ref."""
    h = re.sub(r"[^A-Za-z0-9-]", "-", handle or "").strip("-")
    if not h:
        raise ValueError("empty handle")
    return h


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def clone_dir():
    return os.path.join(data_dir(), "courses")


def worktree_path(handle):
    if handle == MAIN:
        return clone_dir()
    return os.path.join(data_dir(), "worktrees", _safe_handle(handle))


def _existing_handles():
    """Handles with a materialized worktree on the volume. The directory name is
    the safe handle, which is also the branch name (see editor_binding), so these
    are ready to pass straight to enqueue_push. Empty if no worktrees yet."""
    wt_root = os.path.join(data_dir(), "worktrees")
    try:
        return [name for name in os.listdir(wt_root)
                if os.path.isdir(os.path.join(wt_root, name))]
    except FileNotFoundError:
        return []


def db_path_for(handle):
    name = MAIN if handle == MAIN else _safe_handle(handle)
    return os.path.join(data_dir(), "db", f"{name}.sqlite")


# --------------------------------------------------------------------------
# Git plumbing
# --------------------------------------------------------------------------

def _git_env():
    env = dict(os.environ)
    # Never block on an interactive credential/host prompt -- fail fast instead.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    # CONFIG is None in local single-user git mode (no collab.json); the deploy key
    # only applies to collab's remote pushes.
    key = (CONFIG or {}).get("ssh_key_path") or os.environ.get("LESSON_GIT_SSH_KEY")
    if key:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    return env


def _git(args, cwd=None, check=False, author=None):
    """Run a git command; return (returncode, combined_output).

    `author` is an optional (name, email) pair stamped as BOTH the author and
    committer via the GIT_*_NAME/EMAIL env vars. Those env vars take precedence
    over `-c user.*` config (and over any ambient git identity), which is exactly
    why we set them here rather than passing `-c` flags -- it guarantees the
    teacher's attribution regardless of the host environment."""
    env = _git_env()
    if author:
        name, email = author
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = name
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = email
    proc = subprocess.run(["git", *args], cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stdout}")
    return proc.returncode, proc.stdout.strip()


_clone_lock = threading.Lock()


def ensure_clone():
    """Make sure the primary clone exists on the volume and is on origin/main."""
    cd = clone_dir()
    with _clone_lock:
        if not os.path.isdir(os.path.join(cd, ".git")):
            os.makedirs(os.path.dirname(cd), exist_ok=True)
            _git(["clone", CONFIG["repo"], cd], check=True)
        _git(["fetch", "origin"], cwd=cd)
        # Keep the primary clone pinned to origin/main -- it doubles as the
        # viewers' read-only corpus, so it must never carry a teacher's edits.
        _git(["checkout", "-B", "main", "origin/main"], cwd=cd)


def git_fetch():
    _git(["fetch", "origin", "--prune"], cwd=clone_dir())


def _branch_exists_local(branch):
    code, _ = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                   cwd=clone_dir())
    return code == 0


def _ref_exists(ref):
    code, _ = _git(["rev-parse", "--verify", "--quiet", ref], cwd=clone_dir())
    return code == 0


# --------------------------------------------------------------------------
# Per-user db cache (built from the worktree corpus)
# --------------------------------------------------------------------------

_db_locks = {}
_db_locks_guard = threading.Lock()


def _db_lock(handle):
    with _db_locks_guard:
        return _db_locks.setdefault(handle, threading.Lock())


def rebuild_db(handle):
    """(Re)build a handle's db cache from its worktree corpus, atomically.

    Builds into a temp file then os.replace()s it into place, so concurrent
    readers (each opening their own short-lived connection) never see a
    half-built db."""
    corpus = worktree_path(handle)
    final = db_path_for(handle)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    tmp = final + ".tmp"
    with _db_lock(handle):
        if os.path.exists(tmp):
            os.remove(tmp)
        # read_course applies schema.sql itself, so a fresh file is enough.
        # hierarchy.py reports parse errors via sys.exit() (SystemExit, which is
        # NOT an Exception), so a malformed corpus file would otherwise escape and
        # kill the request thread. Convert any failure into a normal error and
        # leave the existing (good) db in place by not replacing it.
        try:
            seed_module.load_corpus(tmp, corpus)
        except (Exception, SystemExit) as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"corpus failed to load: {e}") from None
        os.replace(tmp, final)


# --------------------------------------------------------------------------
# Bindings: resolve (db_path, courses_root) for the acting user
# --------------------------------------------------------------------------

def editor_binding(handle, name, email):
    """Ensure an editor's sandbox (branch + worktree + db) exists, returning
    (db_path, courses_root). No network unless the sandbox must be created."""
    handle = _safe_handle(handle)
    wt = worktree_path(handle)
    db = db_path_for(handle)
    if not os.path.isdir(wt):
        ensure_clone()
        # Base the branch on origin/<handle> if it already exists (returning
        # teacher) -- and let it track that. For a new teacher we fork from
        # origin/main but pass --no-track: the branch must NOT track main (a bare
        # push/pull would then hit main); the first `push -u` sets origin/<handle>
        # as its upstream instead.
        if _ref_exists(f"origin/{handle}"):
            start, track = f"origin/{handle}", []
        else:
            start, track = "origin/main", ["--no-track"]
        if not _branch_exists_local(handle):
            _git(["branch", *track, handle, start], cwd=clone_dir(), check=True)
        os.makedirs(os.path.dirname(wt), exist_ok=True)
        _git(["worktree", "add", wt, handle], cwd=clone_dir(), check=True)
    if not os.path.exists(db):
        rebuild_db(handle)
    return db, wt


_main_lock = threading.Lock()
_main_head = [None]   # the origin/main commit the _main db was last built from


def viewer_binding():
    """(db_path, courses_root) for the shared read-only main view, built lazily and
    refreshed when origin/main advances."""
    with _main_lock:
        if not os.path.isdir(os.path.join(clone_dir(), ".git")):
            ensure_clone()
        db = db_path_for(MAIN)
        _, head = _git(["rev-parse", "HEAD"], cwd=clone_dir())
        if not os.path.exists(db) or _main_head[0] != head:
            rebuild_db(MAIN)
            _main_head[0] = head
        return db, clone_dir()


def refresh_main():
    """Fetch and, if origin/main moved, re-point the primary clone and rebuild the
    viewers' db. Called by the background timer and after relevant changes."""
    with _main_lock:
        git_fetch()
        _, before = _git(["rev-parse", "HEAD"], cwd=clone_dir())
        _git(["checkout", "-B", "main", "origin/main"], cwd=clone_dir())
        _, after = _git(["rev-parse", "HEAD"], cwd=clone_dir())
        if after != before or not os.path.exists(db_path_for(MAIN)):
            rebuild_db(MAIN)
            _main_head[0] = after


# --------------------------------------------------------------------------
# Action buffer -> commit messages
# --------------------------------------------------------------------------

_actions = {}            # handle -> [phrase, ...] since their last commit
_actions_guard = threading.Lock()


def record_action(handle, phrase):
    with _actions_guard:
        _actions.setdefault(handle, []).append(phrase)


def _take_actions(handle):
    with _actions_guard:
        return _actions.pop(handle, [])


def _compose_message(handle, fallback):
    actions = _take_actions(handle)
    if not actions:
        return fallback
    if len(actions) == 1:
        return actions[0]
    return f"{len(actions)} edits\n\n" + "\n".join(f"- {a}" for a in actions)


# --------------------------------------------------------------------------
# Commit + push
# --------------------------------------------------------------------------

# Serialize all commits: the debounced autosave timer and the immediate structural
# commits both run `git add -A` + commit, and concurrent git in one repo collides
# on index.lock. Coarse (one lock for everything) but commits are brief.
_commit_lock = threading.Lock()


# Public alias: app.py composes a commit message from the buffered edit phrases.
compose_message = _compose_message


def commit_repo(repo_dir, message, author=None, push_key=None):
    """Stage the whole repo at `repo_dir` and, if anything changed, commit it and
    (if `push_key`) enqueue a push for that handle. The shared commit primitive for
    both collab worktrees and the local single-user courses repo.

    `message` is a string, or a thunk called ONLY when there's a diff (so composing
    from the action buffer doesn't consume it on a no-op). `author` is (name, email)
    or None to use the ambient git identity (local mode commits as whoever runs the
    server). Returns the commit subject, or None when nothing changed."""
    with _commit_lock:
        _git(["add", "-A"], cwd=repo_dir)
        code, _ = _git(["diff", "--cached", "--quiet"], cwd=repo_dir)
        if code == 0:
            return None   # nothing staged
        msg = message() if callable(message) else message
        _git(["commit", "-m", msg], cwd=repo_dir, check=True, author=author)
    if push_key:
        enqueue_push(push_key)
    return msg.splitlines()[0]


def commit_and_push(handle, name, email, fallback_message):
    """Collab: commit a teacher's worktree with the buffered edit phrases composed
    into the message, and enqueue a push. Returns the commit subject, or None."""
    handle = _safe_handle(handle)
    return commit_repo(worktree_path(handle),
                       lambda: _compose_message(handle, fallback_message),
                       author=(name or handle, email or _noreply(handle)),
                       push_key=handle)


def commit_worktree(handle, name, email, message):
    """Collab: commit a teacher's worktree with an explicit `message` and enqueue a
    push -- for discrete structural changes. Returns the subject, or None."""
    handle = _safe_handle(handle)
    return commit_repo(worktree_path(handle), message,
                       author=(name or handle, email or _noreply(handle)),
                       push_key=handle)


def _noreply(handle):
    return f"{_safe_handle(handle)}@users.noreply.github.com"


def push_status(handle):
    """(published, pending): whether the editor's branch exists on the remote yet
    (origin/<handle>), and how many local commits aren't on it. When the branch
    isn't published, `pending` is the whole history (there's no remote ref to diff
    against) -- callers should treat that as the 'establishing the branch' state
    rather than a literal pending-edit count. Drives the sidebar push banner."""
    handle = _safe_handle(handle)
    wt = worktree_path(handle)
    published = bool(_ref_exists(f"origin/{handle}"))
    rng = f"origin/{handle}..{handle}" if published else handle
    code, out = _git(["rev-list", "--count", rng], cwd=wt)
    try:
        return published, (int(out) if code == 0 else 0)
    except ValueError:
        return published, 0


def unpushed_count(handle):
    """How many commits on <handle> aren't yet on origin/<handle>. See push_status."""
    return push_status(handle)[1]


# Background push worker: a queue of handles to push. Pushes coalesce (a handle
# already queued isn't queued twice) and retry with backoff. The commit is
# always safe on the volume, so a failed push just leaves work pending.
_push_q = queue.Queue()
_queued = set()
_queued_guard = threading.Lock()
_push_failed = {}        # handle -> last push error (for the banner), or absent


def enqueue_push(handle):
    with _queued_guard:
        if handle in _queued:
            return
        _queued.add(handle)
    _push_q.put((handle, 0))


def push_error(handle):
    return _push_failed.get(handle)


def _push_once(handle):
    wt = worktree_path(handle)
    # -u so the branch tracks origin/<handle> after the first push, rather than
    # staying pointed at origin/main (its fork point). Harmless once set.
    code, out = _git(["push", "-u", "origin", handle], cwd=wt)
    return code == 0, out


def _push_worker():
    while True:
        handle, attempt = _push_q.get()
        with _queued_guard:
            _queued.discard(handle)
        try:
            ok, out = _push_once(handle)
        except Exception as e:      # never let the worker thread die
            ok, out = False, str(e)
        if ok:
            _push_failed.pop(handle, None)
        else:
            _push_failed[handle] = out
            print(f"collab: push {handle} failed (attempt {attempt + 1}): {out}",
                  file=sys.stderr)
            if attempt < 5:
                delay = min(60, 2 ** attempt)
                threading.Timer(
                    delay, lambda: _requeue(handle, attempt + 1)).start()
        _push_q.task_done()


def _requeue(handle, attempt):
    with _queued_guard:
        if handle in _queued:
            return
        _queued.add(handle)
    _push_q.put((handle, attempt))


# --------------------------------------------------------------------------
# Debounced autosave: collect edits under a `key` and, after `delay` seconds of
# quiet, run `flush(course)` for each touched course (write db -> repo + commit).
# Keyed + callback-driven so both collab (key = handle, flush pushes) and local
# single-user git (key = "_local", flush commits to the courses repo, no push)
# share it. The flush callback is built by app.py, capturing the repo/db/author at
# schedule time so the timer thread needs no request context.
# --------------------------------------------------------------------------

_autosave_guard = threading.Lock()
_autosave = {}   # key -> {"courses": set, "flush": callable, "timer": Timer}


def autosave_seconds():
    return int(CONFIG.get("autosave_seconds", 2)) if enabled() else 0


def schedule_autosave(key, delay, course, flush):
    """Mark `course` dirty under `key` and (re)arm its debounce timer. `flush(course)`
    is invoked per dirty course when the timer fires. A no-op if delay <= 0."""
    if not delay:
        return
    with _autosave_guard:
        st = _autosave.setdefault(key, {"courses": set(), "flush": None, "timer": None})
        st["courses"].add(course)
        st["flush"] = flush
        if st["timer"] is not None:
            st["timer"].cancel()
        t = threading.Timer(delay, lambda: _autosave_fire(key))
        t.daemon = True
        st["timer"] = t
        t.start()


def _autosave_fire(key):
    with _autosave_guard:
        st = _autosave.get(key)
        if not st:
            return
        courses, flush = st["courses"], st["flush"]
        st["courses"] = set()
        st["timer"] = None
    for course in courses:
        try:
            flush(course)
        except Exception as e:                             # never kill the timer thread
            print(f"collab: autosave {key}/{course} failed: {e}", file=sys.stderr)


def cancel_autosave(key):
    """Drop any pending debounce timer + dirty set for `key`. Used by Sync, which
    commits everything synchronously, so the deferred autosave has nothing to do."""
    with _autosave_guard:
        st = _autosave.get(key)
        if st:
            if st["timer"] is not None:
                st["timer"].cancel()
            st["timer"] = None
            st["courses"] = set()


# --------------------------------------------------------------------------
# Sync: make the editor's branch consistent with GitHub -- merge origin/main in
# (never rebase) and push, both SYNCHRONOUSLY. The caller commits pending edits
# first; this returns only once GitHub is (or isn't) up to date.
# --------------------------------------------------------------------------

def sync(handle, name, email):
    """Merge origin/main into the editor's branch, then push -- synchronously.
    Returns {ok, updated, conflict, pushed, files, message}. `ok` is False on a
    merge conflict or a failed push (so the caller can surface it)."""
    handle = _safe_handle(handle)
    wt = worktree_path(handle)
    git_fetch()
    updated = False
    # Merge origin/main unless the branch already contains it.
    code, _ = _git(["merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=wt)
    if code != 0:
        author = (name or handle, email or _noreply(handle))
        mcode, _out = _git(["merge", "--no-edit", "origin/main"], cwd=wt, author=author)
        if mcode != 0:
            # Surface the conflicted files; abort so the sandbox stays usable.
            _, files = _git(["diff", "--name-only", "--diff-filter=U"], cwd=wt)
            _git(["merge", "--abort"], cwd=wt)
            return {"ok": False, "updated": False, "conflict": True, "pushed": False,
                    "files": [f for f in files.splitlines() if f],
                    "message": "Merge conflict with main; resolve on GitHub. "
                               "(Your edits are committed and safe.)"}
        try:
            rebuild_db(handle)
        except RuntimeError as e:
            # Merge landed but the merged corpus is malformed; still push so the
            # commit isn't stranded, and report.
            ok, out = _push_once(handle)
            return {"ok": False, "updated": True, "conflict": False, "pushed": ok,
                    "message": f"Merged main, but the corpus didn't load: {e}"
                               + ("" if ok else f" (push also failed: {out})")}
        updated = True
    # Push synchronously so Sync returns only once GitHub has the branch.
    ok, out = _push_once(handle)
    if updated and ok:
        msg = "Merged the latest from main and pushed your branch."
    elif updated:
        msg = f"Merged main, but the push failed: {out}"
    elif ok:
        msg = "Pushed your branch — already up to date with main."
    else:
        msg = f"Up to date with main, but the push failed: {out}"
    return {"ok": ok, "updated": updated, "conflict": False, "pushed": ok,
            "message": msg}


# --------------------------------------------------------------------------
# GitHub OAuth (login only; pushing uses the deploy key, not user tokens)
# --------------------------------------------------------------------------

def oauth_authorize_url(state, redirect_uri):
    q = urllib.parse.urlencode({
        "client_id": CONFIG["github_oauth"]["client_id"],
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
    })
    return f"https://github.com/login/oauth/authorize?{q}"


def _post_json(url, data, headers):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _get_json(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "lesson-planning",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def oauth_exchange(code, redirect_uri):
    """Exchange an OAuth code for an access token."""
    data = _post_json(
        "https://github.com/login/oauth/access_token",
        {"client_id": CONFIG["github_oauth"]["client_id"],
         "client_secret": CONFIG["github_oauth"]["client_secret"],
         "code": code, "redirect_uri": redirect_uri},
        {"Accept": "application/json", "User-Agent": "lesson-planning"})
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"OAuth exchange failed: {data}")
    return token


def github_user(token):
    """(handle, display_name, email) for the authenticated GitHub user."""
    u = _get_json("https://api.github.com/user", token)
    handle = u["login"]
    name = u.get("name") or handle
    email = u.get("email")
    if not email:
        try:
            emails = _get_json("https://api.github.com/user/emails", token)
            primary = next((e for e in emails if e.get("primary")), None)
            email = (primary or (emails[0] if emails else {})).get("email")
        except Exception:
            email = None
    return handle, name, email or _noreply(handle)


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def _materialize_ssh_key():
    """If the deploy key is supplied as a secret (LESSON_DEPLOY_KEY -- the key's
    *contents*, not a path), write it to the volume so git can use it, removing
    the need to copy the key onto the machine by hand. Writes to the configured
    `ssh_key_path` (default <data_dir>/deploy_key) with 0600 perms (ssh refuses
    looser ones) and points the config at it. Re-running with new contents
    rotates the key; a key already on the volume with no secret set is untouched."""
    contents = os.environ.get("LESSON_DEPLOY_KEY")
    if not contents:
        return
    path = CONFIG.get("ssh_key_path") or os.path.join(data_dir(), "deploy_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # ssh wants a trailing newline on the key file.
    data = contents if contents.endswith("\n") else contents + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(path, 0o600)   # in case the file pre-existed with looser perms
    CONFIG["ssh_key_path"] = path


def startup():
    """Bring the deployment up: ensure the clone, start the push worker and the
    main-refresh timer, build the viewers' db. Safe to call once at import."""
    if not enabled():
        return
    _materialize_ssh_key()
    try:
        ensure_clone()
    except Exception as e:
        print(f"collab: initial clone/fetch failed: {e}", file=sys.stderr)
    threading.Thread(target=_push_worker, daemon=True,
                     name="collab-push").start()
    # Flush commits that didn't get pushed before the last shutdown. The push
    # queue is in-memory, so a deploy/restart with pushes still pending would
    # strand those commits on the volume (safe, just unpushed). Re-enqueue each
    # existing worktree through the normal per-handle push path: non-force, with
    # retry/backoff and the pending-push badge, and a no-op when nothing's
    # pending. Non-blocking -- the worker drains it in the background, so a
    # GitHub hiccup here just leaves the commits for the next save.
    for handle in _existing_handles():
        enqueue_push(handle)
    _start_main_timer()
    try:
        viewer_binding()       # warm the read-only main db
    except Exception as e:
        print(f"collab: building main view failed: {e}", file=sys.stderr)


def _start_main_timer():
    interval = max(60, int(CONFIG.get("main_refresh_seconds", 300)))

    def tick():
        try:
            refresh_main()
        except Exception as e:
            print(f"collab: main refresh failed: {e}", file=sys.stderr)
        finally:
            t = threading.Timer(interval, tick)
            t.daemon = True
            t.start()

    t = threading.Timer(interval, tick)
    t.daemon = True
    t.start()
