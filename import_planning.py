"""Load the planning tables from export TSVs back into the database.

The inverse of export_planning.py: reads each <table>.tsv (whose header row names
the columns) and replaces that table's rows. Reference `nodes`/`hierarchies` are
regenerated from the hierarchy markdown by load_nodes.py and NOT replaced here;
the TSVs carry only the authored OUTLINE hierarchies, reloaded by a scoped delete.
Together these let you rebuild a database from scratch from version-controlled
inputs:

    sqlite3 db.db < lesson-planning/schema.sql              # empty tables (clean schema)
    uv run load_nodes.py my-course-hierarchy.md db.db      # rebuild `nodes`
    uv run import_planning.py db.db lesson-planning/export/ # reload the planning tables

An empty TSV cell becomes NULL for a nullable column, or stays "" for a NOT NULL
column (so e.g. a lesson's empty title/learning_objective round-trips correctly).
"""

import argparse
import csv
import os
import sqlite3

# Load order: referenced tables before the ones that point at them. (Foreign
# keys aren't enforced, but this keeps the order sensible.) `nodes` and
# `hierarchies` are exported only for OUTLINE hierarchies, so they are reloaded
# by a SCOPED delete (only the hierarchies present in the TSV) to avoid clobbering
# the reference rows that load_nodes.py already wrote.
TABLES = ["objectives", "hierarchies", "hierarchy_targets", "course_objectives",
          "nodes", "node_attr", "coverage"]
SCOPED = {"nodes", "hierarchies"}  # delete only the TSV's hierarchies, not all rows


def load(db_path, in_dir):
    conn = sqlite3.connect(db_path)
    loaded = []
    try:
        for table in TABLES:
            path = os.path.join(in_dir, f"{table}.tsv")
            if not os.path.exists(path):
                continue
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter="\t")
                cols = next(reader, None)
                if not cols:
                    continue
                rows = list(reader)
            # Per-column NOT NULL flags, to decide whether "" means NULL or "".
            notnull = {r[1]: r[3] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            def cell(col, v):
                return None if v == "" and not notnull.get(col, 0) else v
            data = [tuple(cell(cols[i], v) for i, v in enumerate(r)) for r in rows]
            if table in SCOPED and "hierarchy" in cols:
                hi = cols.index("hierarchy")
                conn.executemany(f'DELETE FROM "{table}" WHERE hierarchy=?',
                                 [(h,) for h in sorted({r[hi] for r in rows})])
            else:
                conn.execute(f'DELETE FROM "{table}"')
            collist = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join(["?"] * len(cols))
            conn.executemany(
                f'INSERT INTO "{table}"({collist}) VALUES ({placeholders})', data)
            loaded.append((table, len(data)))
        conn.commit()
    finally:
        conn.close()
    return loaded


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("database", help="SQLite database file (schema already applied)")
    p.add_argument("in_dir", help="directory of <table>.tsv files (from export_planning.py)")
    args = p.parse_args()
    loaded = load(args.database, args.in_dir)
    for table, n in loaded:
        print(f"  {table}: {n} rows")
    print(f"loaded {len(loaded)} planning tables from {args.in_dir} into {args.database}")


if __name__ == "__main__":
    main()
