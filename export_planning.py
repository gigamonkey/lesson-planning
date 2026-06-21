"""Export the lesson-planning database to git-diffable TSV snapshots.

The lesson-planning database is the live working store the app edits; this dumps
its planning tables to stable, sorted TSV files so the canonical state stays
reviewable in a PR and reproducible. The `nodes` table is intentionally NOT
exported -- it is regenerated from the hierarchy node-list JSON by load_nodes.py.

    uv run export_planning.py db.db export/

Writes one <table>.tsv per planning table (objectives, hierarchies,
hierarchy_targets, course_objectives, nodes, node_attr, coverage), each with a
header row and rows sorted for a deterministic diff. `nodes` and `hierarchies`
are filtered to the authored OUTLINE hierarchies (reference rows come from
markdown), while `coverage` is exported in full (reference + outline edges).
"""

import argparse
import csv
import os
import sqlite3

# Planning tables to export: columns to dump, columns to sort by (stable diff),
# and an optional WHERE clause. `nodes` and `hierarchies` are exported only for
# OUTLINE hierarchies -- reference rows are regenerated from markdown by load_nodes.
OUTLINE = "hierarchy IN (SELECT hierarchy FROM hierarchies WHERE editable=1)"
TABLES = {
    "objectives": (["uuid", "text", "status"], ["uuid"], None),
    "hierarchies": (["hierarchy", "course", "kind", "editable", "title", "source"],
                    ["hierarchy"], "editable=1"),
    "hierarchy_targets": (["outline", "reference"], ["outline", "reference"], None),
    "course_objectives": (["course", "uuid", "position"], ["course", "uuid"], None),
    "nodes": (["hierarchy", "node_id", "parent_id", "level", "is_leaf", "ordinal", "text"],
              ["hierarchy", "ordinal", "node_id"], OUTLINE),
    "node_attr": (["hierarchy", "node_id", "name", "value"],
                  ["hierarchy", "node_id", "name"], None),
    "coverage": (["hierarchy", "uuid", "node_id"], ["hierarchy", "node_id", "uuid"], None),
}


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def export(db_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    written = []
    try:
        for table, (cols, order, where) in TABLES.items():
            if not table_exists(conn, table):
                continue
            select = f'SELECT {", ".join(cols)} FROM "{table}" ' \
                     + (f'WHERE {where} ' if where else "") \
                     + f'ORDER BY {", ".join(order)}'
            rows = conn.execute(select).fetchall()
            path = os.path.join(out_dir, f"{table}.tsv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter="\t", lineterminator="\n")
                w.writerow(cols)
                w.writerows(rows)
            written.append((table, len(rows)))
    finally:
        conn.close()
    # Prune stale <table>.tsv files for tables that are no longer exported.
    keep = {f"{t}.tsv" for t in TABLES}
    pruned = [fn for fn in sorted(os.listdir(out_dir))
              if fn.endswith(".tsv") and fn not in keep]
    for fn in pruned:
        os.remove(os.path.join(out_dir, fn))
    return written, pruned


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("out_dir", help="directory to write <table>.tsv files")
    args = parser.parse_args()

    written, pruned = export(args.database, args.out_dir)
    for table, n in written:
        print(f"  {table}.tsv: {n} rows")
    for fn in pruned:
        print(f"  removed stale {fn}")
    print(f"exported {len(written)} tables to {args.out_dir}")


if __name__ == "__main__":
    main()
