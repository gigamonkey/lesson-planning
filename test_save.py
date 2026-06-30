#!/usr/bin/env python3
"""Self-contained checks for the manual-Save model (plans/manual-save.md). No test
framework: run directly.

    uv run test_save.py

Boots the app against a throwaway copy of examples/ (a non-repo, so app copies it
into a disposable git repo -- never touches the real one) with the file-autosave
timer disabled, and drives the test client to assert: edits don't auto-commit; the
commit page's suggested message reflects the edits; /flush writes files without
committing; /save commits (and is a no-op when clean); single-user /sync is
lossless.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Must be set before importing app (it resolves these at import time).
os.environ["LESSON_COURSES_DIR"] = os.path.join(HERE, "examples")
os.environ["LESSON_DB"] = tempfile.mktemp(suffix=".db")
# A long debounce: the autosave registers a pending file-write (so /flush has
# something to do) but the timer won't fire during this fast test -- we drive the
# writes explicitly via /flush and /save.
os.environ["LESSON_AUTOSAVE_SECONDS"] = "30"
os.environ.pop("LESSON_COLLAB_CONFIG", None)  # ensure single-user (local-git) mode

import app  # noqa: E402

REPO = app.COURSES_ROOT
_failures = 0


def check(label, ok):
    global _failures
    print(f"{'ok' if ok else 'FAIL'} - {label}")
    if not ok:
        _failures += 1


def _git(*args):
    return subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True).stdout.strip()


def main():
    check("running in throwaway-demo local-git mode", app.DEMO_MODE and app.LOCAL_GIT)
    c = app.app.test_client()

    bar = c.get("/savebar").data
    check("savebar renders a Commit link to /commit", b"savebtn" in bar and b"/commit" in bar)
    check("not dirty before any edit", not app.collab.is_dirty("_local"))
    check("clean commit page says no changes",
          "No changes to commit" in c.get("/commit").get_data(as_text=True))

    base_commits = _git("rev-list", "--count", "HEAD")

    # An edit updates the db and marks dirty -- but does NOT commit.
    c.post("/widgets/unit/new", data={})
    check("edit marks the workspace dirty", app.collab.is_dirty("_local"))
    check("savebar shows the dirty state", b"dirty" in c.get("/savebar").data)
    page = c.get("/commit").get_data(as_text=True)
    check("commit page suggests a message from the edit", "added a unit" in page)
    check("commit page renders a diff", 'class="diff"' in page and "d-add" in page)
    check("edit did NOT auto-commit", _git("rev-list", "--count", "HEAD") == base_commits)

    # /flush writes the pending files to disk, still without committing.
    c.post("/flush")
    check("flush wrote files (working tree dirty)", bool(_git("status", "--porcelain")))
    check("flush did NOT commit", _git("rev-list", "--count", "HEAD") == base_commits)

    # Save commits with the supplied message and clears dirty.
    c.post("/save", data={"message": "Add a unit (test)"}, follow_redirects=True)
    check("save committed", _git("rev-list", "--count", "HEAD") != base_commits)
    check("save used the supplied message", _git("log", "-1", "--pretty=%s") == "Add a unit (test)")
    check("save cleared the dirty flag", not app.collab.is_dirty("_local"))
    check("working tree clean after save", not _git("status", "--porcelain"))

    # A second Save with nothing to commit is a friendly no-op.
    r = c.post("/save", data={"message": "noop"}, follow_redirects=True)
    check("no-op commit says 'Nothing to commit'", "Nothing to commit" in r.get_data(as_text=True))

    # Single-user Sync is lossless: it flushes db -> files, then reloads from disk.
    with app.db() as conn:
        before = conn.execute("SELECT count(*) FROM nodes WHERE course='widgets'"
                              " AND level='unit'").fetchone()[0]
    c.post("/widgets/unit/new", data={})
    c.post("/sync", follow_redirects=True)
    with app.db() as conn:
        after = conn.execute("SELECT count(*) FROM nodes WHERE course='widgets'"
                             " AND level='unit'").fetchone()[0]
    check("sync preserved the just-made edit (lossless)", after == before + 1)

    if _failures:
        print(f"\n{_failures} check(s) failed")
        sys.exit(1)
    print("\nok - all save checks passed")


if __name__ == "__main__":
    main()
