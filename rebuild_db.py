"""Rebuild the lesson-planning database from scratch, from a markdown corpus.

Recreates the database in two steps:
  1. schema.sql -> empty tables (the clean, canonical schema)
  2. a corpus   -> every course (via seed.load_corpus / plan_io.read_course)

A **corpus** is a directory of course directories (see `FORMAT.md` / `seed.py`):
each holds its reference hierarchy markdown, its `plan.md` outline, and the
`objectives.tsv` / `coverage.tsv`. This is the inverse of exporting each course
with `plan_io.write_course`, so a rebuild reproduces the database exactly from
the git-tracked files.

The existing database file is DELETED first, so anything in it not yet exported
to the corpus is lost -- export (and stop the app) before rebuilding.

    uv run rebuild_db.py                       # default corpus 'courses/'
    uv run rebuild_db.py --corpus examples     # the bundled widgets example
    uv run rebuild_db.py --db /tmp/x.db --corpus mycorpus
"""

import argparse
import os
import sqlite3

import seed as seed_module


def populate(db_path, corpus):
    """Load every course in `corpus` into an already-schema'd db (non-destructive;
    the app's 'restore from disk' calls this). Returns the loaded course dirs."""
    dirs = seed_module.course_dirs(corpus)
    seed_module.load_corpus(db_path, corpus)
    return dirs


def rebuild(db_path, schema_path, corpus):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print(f"applied {schema_path} -> fresh {db_path}")

    dirs = populate(db_path, corpus)
    print(f"rebuilt {db_path} from {len(dirs)} course(s) in {corpus}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="db.db")
    p.add_argument("--schema", default="schema.sql")
    p.add_argument("--corpus", default="courses")
    args = p.parse_args()
    rebuild(args.db, args.schema, args.corpus)


if __name__ == "__main__":
    main()
