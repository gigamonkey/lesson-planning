#!/usr/bin/env python3
"""Self-contained checks for the external-change guard (HEAD-based). No test
framework: run directly.

    uv run test_conflict.py

Simulates the corner case: you make an in-app edit, then the course files change
on disk under you (a `git pull`, faked here as a direct commit). The guard must
NOT clobber the disk version -- the autosave skips and flags a conflict, Commit
refuses, and Reload resolves it by taking the disk version.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["LESSON_COURSES_DIR"] = os.path.join(HERE, "examples")
os.environ["LESSON_DB"] = tempfile.mktemp(suffix=".db")
os.environ["LESSON_AUTOSAVE_SECONDS"] = "30"   # timer registers a pending write; won't fire

import app  # noqa: E402

REPO = app.COURSES_ROOT
PLAN = os.path.join(REPO, "widgets", "plan.md")
MARK = "<!-- external edit -->"
_failures = 0


def check(label, ok):
    global _failures
    print(f"{'ok' if ok else 'FAIL'} - {label}")
    if not ok:
        _failures += 1


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True).stdout.strip()


def main():
    c = app.app.test_client()
    check("no conflict at start", not app.collab.has_conflict(REPO))

    # An in-app edit (db only; autosave timer won't fire during the test).
    c.post("/widgets/unit/new", data={})

    # Simulate an external pull: change a course file and commit it directly,
    # bypassing the app. The working tree is clean (the edit is db-only), so this
    # commits cleanly -- exactly what a real `git pull` would land.
    with open(PLAN, "a", encoding="utf-8") as f:
        f.write("\n" + MARK + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", "External change")
    commits = git("rev-list", "--count", "HEAD")

    check("head_moved detects the external commit", app.collab.head_moved(REPO))

    # The autosave flush must NOT clobber the externally-changed file.
    c.post("/flush")
    check("flush left the external edit intact", MARK in open(PLAN, encoding="utf-8").read())
    check("flush flagged a conflict", app.collab.has_conflict(REPO))

    # The sidebar surfaces it.
    check("savebar warns about the disk change",
          b"changed on disk" in c.get("/savebar").data)

    # Commit refuses (would clobber) and makes no new commit.
    r = c.post("/save", data={"message": "mine"}, follow_redirects=True)
    check("commit refused on conflict", "changed on disk" in r.get_data(as_text=True))
    check("commit made no new commit", git("rev-list", "--count", "HEAD") == commits)
    check("external edit still intact after refused commit",
          MARK in open(PLAN, encoding="utf-8").read())

    # Reload resolves it: take the disk version, clear the conflict.
    c.post("/sync", follow_redirects=True)
    check("reload cleared the conflict", not app.collab.has_conflict(REPO))
    check("head no longer 'moved' after reload", not app.collab.head_moved(REPO))
    check("external edit preserved through reload",
          MARK in open(PLAN, encoding="utf-8").read())

    # After resolving, a fresh edit + Commit works normally again.
    c.post("/widgets/unit/new", data={})
    c.post("/save", data={"message": "after resolve"}, follow_redirects=True)
    check("commit works again after reload", git("rev-list", "--count", "HEAD") != commits)

    if _failures:
        print(f"\n{_failures} check(s) failed")
        sys.exit(1)
    print("\nok - all conflict-guard checks passed")


if __name__ == "__main__":
    main()
