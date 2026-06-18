"""Rebuild the lesson-planning database from scratch, from version-controlled inputs.

Recreates the database in three steps:
  1. schema.sql            -> empty tables (the clean, canonical schema)
  2. hierarchy markdown(s) -> the `nodes` table (via load_nodes)
  3. an export dir         -> the planning tables (via import_planning)

The existing database file is DELETED first, so anything in it that is not in the
export dir is lost -- Export a snapshot (and stop the app) before rebuilding.

    uv run rebuild_db.py
    uv run rebuild_db.py --db /tmp/x.db --export lesson-planning/export/ \
        csa/ced-2025-hierarchy.md ib/ib-hierarchy.md
"""

import argparse
import os
import sqlite3

import import_planning
import load_nodes
from hierarchy import parse_sections

DEFAULT_HIERARCHIES = [
    "csa/ced-2025-hierarchy.md",
    "csp/ced-hierarchy.md",
    "ib/ib-hierarchy.md",
]


def rebuild(db_path, schema_path, export_dir, hierarchies):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print(f"applied {schema_path} -> fresh {db_path}")

    for hf in hierarchies:
        if not os.path.exists(hf):
            print(f"  skip nodes (missing): {hf}")
            continue
        with open(hf) as f:
            flavor, sections = parse_sections(f.read())
        rows = load_nodes.build_rows(flavor, flavor, sections)
        load_nodes.load(db_path, flavor, rows)
        print(f"  nodes: {hf} -> course {flavor!r} ({len(rows)} nodes)")

    for table, n in import_planning.load(db_path, export_dir):
        print(f"  {table}: {n} rows")
    print(f"rebuilt {db_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("hierarchy", nargs="*", default=None,
                   help="hierarchy markdown file(s) for the nodes table "
                        "(default: the known CED/IB files that exist)")
    p.add_argument("--db", default="lesson-planning/db.db")
    p.add_argument("--schema", default="lesson-planning/schema.sql")
    p.add_argument("--export", default="lesson-planning/export/")
    args = p.parse_args()
    hierarchies = args.hierarchy or DEFAULT_HIERARCHIES
    rebuild(args.db, args.schema, args.export, hierarchies)


if __name__ == "__main__":
    main()
