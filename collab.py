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
    key = CONFIG.get("ssh_key_path") or os.environ.get("LESSON_GIT_SSH_KEY")
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
# Bindings: resolve (db_path, corpus_dir) for the acting user
# --------------------------------------------------------------------------

def editor_binding(handle, name, email):
    """Ensure an editor's sandbox (branch + worktree + db) exists, returning
    (db_path, corpus_dir). No network unless the sandbox must be created."""
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
    """(db_path, corpus_dir) for the shared read-only main view, built lazily and
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

def commit_and_push(handle, name, email, fallback_message):
    """Commit the worktree (author = the teacher) and enqueue a push. The caller
    has already written the corpus files (plan_io.write_course); we sweep the
    whole worktree with `add -A` so any reference markdown lands too.

    Returns the commit subject if a commit was made, else None."""
    handle = _safe_handle(handle)
    wt = worktree_path(handle)
    _git(["add", "-A"], cwd=wt)
    code, _ = _git(["diff", "--cached", "--quiet"], cwd=wt)
    if code == 0:
        return None   # nothing staged
    message = _compose_message(handle, fallback_message)
    author = (name or handle, email or _noreply(handle))
    _git(["commit", "-m", message], cwd=wt, check=True, author=author)
    enqueue_push(handle)
    return message.splitlines()[0]


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
# Sync: merge origin/main into a worktree branch (never rebase)
# --------------------------------------------------------------------------

def sync(handle, name, email):
    """Fetch and merge origin/main into the editor's branch. Returns a dict:
    {ok, updated, conflict, files, message}. On a clean merge the db is rebuilt
    and the (merge) commit is pushed."""
    handle = _safe_handle(handle)
    wt = worktree_path(handle)
    git_fetch()
    _, before = _git(["rev-parse", "HEAD"], cwd=wt)
    _, main = _git(["rev-parse", "origin/main"], cwd=wt)
    # Already contains origin/main? Nothing to do.
    code, _ = _git(["merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=wt)
    if code == 0:
        return {"ok": True, "updated": False, "conflict": False,
                "message": "Already up to date with main."}
    author = (name or handle, email or _noreply(handle))
    code, out = _git(["merge", "--no-edit", "origin/main"], cwd=wt, author=author)
    if code != 0:
        # Surface the conflicted files; leave the merge in progress for the user
        # to resolve on GitHub or locally. (Abort so the sandbox stays usable.)
        _, files = _git(["diff", "--name-only", "--diff-filter=U"], cwd=wt)
        _git(["merge", "--abort"], cwd=wt)
        return {"ok": False, "updated": False, "conflict": True,
                "files": [f for f in files.splitlines() if f],
                "message": "Merge conflict with main; resolve on GitHub."}
    try:
        rebuild_db(handle)
    except RuntimeError as e:
        # The merge landed but the merged corpus is malformed. The commit is on
        # the branch (pushable); the live db kept its previous good state.
        enqueue_push(handle)
        return {"ok": False, "updated": True, "conflict": False,
                "message": f"Merged main, but the corpus didn't load: {e}"}
    enqueue_push(handle)
    return {"ok": True, "updated": True, "conflict": False,
            "message": "Merged the latest from main."}


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
