"""Rebuild the lesson-planning database from scratch, from a markdown courses directory.

Recreates the database in two steps:
  1. schema.sql -> empty tables (the clean, canonical schema)
  2. a courses directory -> every course (via seed.load_courses / plan_io.read_course)

A **courses directory** is a directory of course directories (see `FORMAT.md` /
`seed.py`): each holds its reference hierarchy markdown, its `plan.md` outline, and
the `objectives.tsv` / `coverage.tsv`. This is the inverse of exporting each course
with `plan_io.write_course`, so a rebuild reproduces the database exactly from
the git-tracked files.

The existing database file is DELETED first, so anything in it not yet written
to disk is lost -- save (and stop the app) before rebuilding.

    uv run rebuild_db.py                        # default 'examples/' (the demo)
    uv run rebuild_db.py --courses ../bhs-cs-courses
    uv run rebuild_db.py --db /tmp/x.db --courses my-courses
"""

import argparse
import os
import sqlite3

import seed as seed_module


def populate(db_path, courses_root):
    """Load every course in `courses_root` into an already-schema'd db (non-destructive;
    the app's 'restore from disk' calls this). Returns the loaded course dirs."""
    dirs = seed_module.course_dirs(courses_root)
    seed_module.load_courses(db_path, courses_root)
    return dirs


def rebuild(db_path, schema_path, courses_root):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print(f"applied {schema_path} -> fresh {db_path}")

    dirs = populate(db_path, courses_root)
    print(f"rebuilt {db_path} from {len(dirs)} course(s) in {courses_root}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="db.db")
    p.add_argument("--schema", default="schema.sql")
    p.add_argument("--courses", default="examples")
    args = p.parse_args()
    rebuild(args.db, args.schema, args.courses)


if __name__ == "__main__":
    main()
