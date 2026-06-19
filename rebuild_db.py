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

# Each spec is a markdown `path` plus optional overrides for the flavor-derived
# defaults (course, kind, hierarchy slug, course_title) -- needed when a file's
# course/slug isn't implied by its flavor, e.g. a course's book outline.
DEFAULT_HIERARCHIES = [
    {"path": "csa/ced-2025-hierarchy.md"},
    {"path": "csp/ced-hierarchy.md"},
    {"path": "ib/ib-hierarchy.md"},
    {"path": "csa/bhsawesome-outline.md", "hierarchy": "csa-book", "course": "csa",
     "course_title": "AP Computer Science A"},
]


def load_reference_nodes(db_path, specs):
    """Load each spec's hierarchy markdown into `nodes` (via load_nodes).

    Returns a list of (path, slug, course, n_rows) for what loaded; a missing file
    yields (path, None, None, None) so callers can report the skip.
    """
    loaded = []
    for spec in specs:
        hf = spec["path"]
        if not os.path.exists(hf):
            loaded.append((hf, None, None, None))
            continue
        with open(hf) as f:
            flavor, sections = parse_sections(f.read())
        m = load_nodes.meta_for(flavor, course=spec.get("course"), kind=spec.get("kind"),
                                slug=spec.get("hierarchy"),
                                course_title=spec.get("course_title"))
        rows = load_nodes.build_rows(m["slug"], flavor, sections)
        load_nodes.load(db_path, m["slug"], m["course"], m["kind"], m["course_title"],
                        rows, source=hf)
        loaded.append((hf, m["slug"], m["course"], len(rows)))
    return loaded


def populate(db_path, export_dir, specs):
    """Load reference nodes from the markdown `specs`, then the planning tables from
    `export_dir` -- the NON-destructive half of a rebuild (no file delete). Safe to
    run against an already-schema'd db (fresh or live). The app calls this for its
    "Restore from version control"; `rebuild` adds the delete + schema around it.

    Returns (node_loads, table_loads) as produced by load_reference_nodes and
    import_planning.load.
    """
    node_loads = load_reference_nodes(db_path, specs)
    table_loads = import_planning.load(db_path, export_dir)
    return node_loads, table_loads


def rebuild(db_path, schema_path, export_dir, specs):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print(f"applied {schema_path} -> fresh {db_path}")

    node_loads, table_loads = populate(db_path, export_dir, specs)
    for hf, slug, course, n in node_loads:
        if slug is None:
            print(f"  skip nodes (missing): {hf}")
        else:
            print(f"  nodes: {hf} -> hierarchy {slug!r} (course {course!r}, {n} nodes)")
    for table, n in table_loads:
        print(f"  {table}: {n} rows")
    print(f"rebuilt {db_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("hierarchy", nargs="*", default=None,
                   help="hierarchy markdown file(s) for the nodes table, with "
                        "flavor-derived course/slug (default: the known CED/IB/book files)")
    p.add_argument("--db", default="lesson-planning/db.db")
    p.add_argument("--schema", default="lesson-planning/schema.sql")
    p.add_argument("--export", default="lesson-planning/export/")
    args = p.parse_args()
    specs = [{"path": h} for h in args.hierarchy] if args.hierarchy else DEFAULT_HIERARCHIES
    rebuild(args.db, args.schema, args.export, specs)


if __name__ == "__main__":
    main()
