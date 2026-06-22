"""Populate a database from a corpus: a directory of course directories.

A **corpus** is a directory whose immediate subdirectories are each one course
(see `plan_io` / `FORMAT.md`): reference hierarchy markdown, the `plan.md`
outline, and `objectives.tsv` / `coverage.tsv`. This module loads them:

  * `seed(db, corpus)`   -- load every course that does NOT already exist
    (idempotent at course granularity; safe to run on every startup);
  * `load_corpus(db, corpus)` -- (re)load every course in the corpus
    (each `read_course` is itself a scoped replace), for "restore from disk".

A course subdirectory is recognized by containing a `plan.md`; its course id
comes from that file's `course:` front-matter key. The corpus root is both the
load source and the export target (`plan_io.write_course`), so the pair
round-trips.

    uv run seed.py <corpus-dir> [db.db]        # load new courses
    uv run seed.py --all <corpus-dir> [db.db]  # reload every course
"""

import argparse
import os
import sqlite3
import sys

import hierarchy
import plan_io


def course_dirs(corpus):
    """Subdirectories of `corpus` that hold a course (contain a plan.md)."""
    if not os.path.isdir(corpus):
        return []
    out = []
    for name in sorted(os.listdir(corpus)):
        path = os.path.join(corpus, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, plan_io.PLAN_FILE)):
            out.append(path)
    return out


def course_id(course_dir):
    """The course id a course directory declares (its plan.md `course:` key)."""
    with open(os.path.join(course_dir, plan_io.PLAN_FILE), encoding="utf-8") as f:
        meta, _ = hierarchy.parse_front_matter(f.read())
    return meta.get("course") or os.path.basename(course_dir)


def _exists(db_path, course):
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='courses'").fetchone():
            return False
        return conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone() is not None
    finally:
        conn.close()


def seed(db_path, corpus, force=False):
    """Load each corpus course. With force=False, skip courses already present."""
    dirs = course_dirs(corpus)
    if not dirs:
        print(f"seed: no course directories in {corpus!r}; nothing to load", file=sys.stderr)
        return
    for cd in dirs:
        course = course_id(cd)
        if not force and _exists(db_path, course):
            print(f"seed: course {course!r} already exists -- skipping", file=sys.stderr)
            continue
        try:
            c, n_refs, n_obj = plan_io.read_course(db_path, cd)
            print(f"seed: loaded course {c!r} from {os.path.basename(cd)} "
                  f"({n_refs} reference(s), {n_obj} objective(s))", file=sys.stderr)
        except Exception as e:  # one broken course must not abort the rest
            print(f"seed: WARN {os.path.basename(cd)}: {e}", file=sys.stderr)


def load_corpus(db_path, corpus):
    """Reload every course in the corpus (each read_course is a scoped replace)."""
    seed(db_path, corpus, force=True)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("corpus", help="corpus directory (subdirs are courses)")
    p.add_argument("database", nargs="?", default="db.db")
    p.add_argument("--all", action="store_true", help="reload every course, not just new ones")
    args = p.parse_args()
    seed(args.database, args.corpus, force=args.all)


if __name__ == "__main__":
    main()
