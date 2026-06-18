"""Seed raw objectives and their CED coverage from an objectives TSV.

Reads a learning-objectives TSV (header plus columns uuid, unit, topic, lo, ek,
objective) and loads it into the lesson-planning database's raw-objective tables:

    objectives(uuid, text, status)                -- the objective text
    course_objectives(course, uuid)               -- which course it belongs to
    coverage(course, uuid, node_id)               -- the CED node it covers

Each row's ek (the leaf id) becomes one coverage edge; rows mapped to 'none' get
no edge. Coverage node_ids are checked against the `nodes` table (load that
first with load_nodes.py) and any that don't resolve are reported -- they are
still inserted, but flag a mislabeled objective or a hierarchy change.

The load is course-scoped: re-running replaces only this course's objectives,
course links, and coverage, so several courses can share one database.

    uv run import_objectives.py csa/learning-objectives/objectives.tsv lesson-planning/db.db
"""

import argparse
import csv
import sqlite3

DDL = [
    """CREATE TABLE IF NOT EXISTS objectives (
         uuid TEXT PRIMARY KEY,
         text TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'active'
       )""",
    """CREATE TABLE IF NOT EXISTS course_objectives (
         course TEXT NOT NULL,
         uuid TEXT NOT NULL REFERENCES objectives(uuid),
         position INTEGER,
         PRIMARY KEY (course, uuid)
       )""",
    """CREATE TABLE IF NOT EXISTS coverage (
         course TEXT NOT NULL,
         uuid TEXT NOT NULL REFERENCES objectives(uuid),
         node_id TEXT NOT NULL,
         PRIMARY KEY (course, uuid, node_id)
       )""",
]


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load(db_path, course, rows):
    conn = sqlite3.connect(db_path)
    try:
        for statement in DDL:
            conn.execute(statement)

        # Replace only this course's rows so multiple courses can share the db.
        old = [u for (u,) in conn.execute(
            "SELECT uuid FROM course_objectives WHERE course = ?", (course,)
        )]
        conn.executemany("DELETE FROM objectives WHERE uuid = ?", [(u,) for u in old])
        conn.executemany("DELETE FROM coverage WHERE uuid = ?", [(u,) for u in old])
        conn.execute("DELETE FROM course_objectives WHERE course = ?", (course,))

        conn.executemany(
            "INSERT OR IGNORE INTO objectives(uuid, text) VALUES (?, ?)",
            [(r["uuid"], r["objective"]) for r in rows],
        )
        conn.executemany(
            "INSERT INTO course_objectives(course, uuid) VALUES (?, ?)",
            [(course, r["uuid"]) for r in rows],
        )
        edges = [
            (course, r["uuid"], r["ek"])
            for r in rows
            if r["ek"] and r["ek"] != "none"
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO coverage VALUES (?, ?, ?)", edges
        )

        # Report coverage edges whose node_id is not in the loaded hierarchy.
        known = {n for (n,) in conn.execute(
            "SELECT node_id FROM nodes WHERE course = ?", (course,)
        )}
        dangling = sorted({e[2] for e in edges if known and e[2] not in known})
        conn.commit()
    finally:
        conn.close()
    return len(rows), len(edges), known, dangling


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="objectives TSV file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--course", default="csa", help="course id (default: csa)")
    args = parser.parse_args()

    rows = read_rows(args.input)
    n, edges, known, dangling = load(args.database, args.course, rows)
    print(
        f"loaded {n} objectives ({edges} coverage edges) for course "
        f"{args.course!r} into {args.database}"
    )
    if not known:
        print("  note: no nodes loaded for this course yet -- run load_nodes.py "
              "to enable coverage checks")
    elif dangling:
        print(f"  warning: {len(dangling)} coverage node_id(s) not found in "
              f"nodes: {', '.join(dangling)}")


if __name__ == "__main__":
    main()
