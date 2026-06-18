"""Import raw objectives into a course's pool, interning by text.

Two input shapes, auto-detected from whether the first non-blank line has a tab:

  * Plain text -- one objective per non-blank line (no tabs, no header). Each
    line becomes a raw objective in the course's pool, with no coverage.

  * TSV table -- a header row naming columns; `objective` is required and
    `node_id` is optional (the coverage target; blank or 'none' = pool only).
    `ek` is accepted as an alias for `node_id`; other columns are ignored. A
    node_id implies its ancestors, so only the node itself is needed -- not the
    path through the hierarchy.

Objectives are interned by exact text: a line/row whose text already exists
reuses that objective (and its uuid) rather than creating a duplicate. So the
import is idempotent -- re-running adds no duplicate objectives, pool
memberships, or coverage edges -- and two rows with the same text collapse onto
one objective (with both coverage edges). uuids are generated; you never supply
them.

Coverage edges go into the course's reference hierarchy (the CED, resolved from
`hierarchies`, default '<course>-ced'); --hierarchy targets another (IB, a book).
node_ids are checked against the loaded `nodes` (load_nodes.py first) and unknown
ones are reported -- still inserted, but flag a mislabeled objective or a change.

    uv run import_objectives.py objectives.txt lesson-planning/db.db --course csa
    uv run import_objectives.py categorized.tsv lesson-planning/db.db --course csa
"""

import argparse
import csv
import io
import sqlite3
import uuid as uuidlib

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
         hierarchy TEXT NOT NULL,
         uuid TEXT NOT NULL REFERENCES objectives(uuid),
         node_id TEXT NOT NULL,
         PRIMARY KEY (hierarchy, uuid, node_id)
       )""",
]


def parse_items(path):
    """Parse an input file into (items, mode).

    items is a list of (text, node_id|None); mode is 'text' or 'table'.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")

    if "\t" not in first:  # plain text: one objective per non-blank line
        return [(ln.strip(), None) for ln in lines if ln.strip()], "text"

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    cols = reader.fieldnames or []
    if "objective" not in cols:
        raise SystemExit(
            f"table input must have an 'objective' column; got: {', '.join(cols)}")
    items = []
    for r in reader:
        text = (r.get("objective") or "").strip()
        if not text:
            continue
        node = (r.get("node_id") or r.get("ek") or "").strip()
        items.append((text, None if node.lower() in ("", "none") else node))
    return items, "table"


def reference_slug(conn, course):
    """The course's reference (CED) hierarchy slug; load_nodes registers it.

    Falls back to the conventional '<course>-ced' if the hierarchy isn't loaded
    yet (the coverage edges still record where the objectives belong).
    """
    try:
        row = conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0 "
            "ORDER BY (kind='ced') DESC, hierarchy LIMIT 1", (course,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return row[0] if row else f"{course}-ced"


def load(db_path, course, items, hierarchy=None, replace=False):
    """Intern `items` (text, node_id) into the course's pool and coverage.

    Returns (ref_slug, stats, dangling) where stats counts what changed and
    dangling is the sorted set of node_ids not present in the target hierarchy.
    """
    conn = sqlite3.connect(db_path)
    try:
        for statement in DDL:
            conn.execute(statement)
        ref = hierarchy or reference_slug(conn, course)

        if replace:
            old = [u for (u,) in conn.execute(
                "SELECT uuid FROM course_objectives WHERE course=?", (course,))]
            conn.executemany("DELETE FROM coverage WHERE hierarchy=? AND uuid=?",
                             [(ref, u) for u in old])
            conn.execute("DELETE FROM course_objectives WHERE course=?", (course,))

        pos = conn.execute("SELECT COALESCE(MAX(position), -1)+1 FROM course_objectives"
                           " WHERE course=?", (course,)).fetchone()[0]
        known = {n for (n,) in conn.execute(
            "SELECT node_id FROM nodes WHERE hierarchy=?", (ref,))}
        stats = {"read": 0, "objectives_new": 0, "pooled": 0, "coverage": 0}
        dangling = set()

        for text, node in items:
            stats["read"] += 1
            row = conn.execute("SELECT uuid FROM objectives WHERE text=?", (text,)).fetchone()
            if row:
                uuid = row[0]
            else:
                uuid = str(uuidlib.uuid4())
                conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (uuid, text))
                stats["objectives_new"] += 1
            if not conn.execute("SELECT 1 FROM course_objectives WHERE course=? AND uuid=?",
                                (course, uuid)).fetchone():
                conn.execute("INSERT INTO course_objectives(course, uuid, position)"
                             " VALUES (?, ?, ?)", (course, uuid, pos))
                pos += 1
                stats["pooled"] += 1
            if node:
                if known and node not in known:
                    dangling.add(node)
                cur = conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id)"
                                   " VALUES (?, ?, ?)", (ref, uuid, node))
                stats["coverage"] += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return ref, stats, sorted(dangling), known


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="objectives file (plain text or TSV table)")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--course", default="csa", help="course id (default: csa)")
    parser.add_argument("--hierarchy",
                        help="coverage hierarchy slug (default: the course's reference)")
    parser.add_argument("--replace", action="store_true",
                        help="clear the course's pool and its coverage in the target "
                             "hierarchy before importing")
    args = parser.parse_args()

    items, mode = parse_items(args.input)
    ref, stats, dangling, known = load(args.database, args.course, items,
                                       hierarchy=args.hierarchy, replace=args.replace)
    print(f"{mode}: read {stats['read']} objectives for course {args.course!r} -> "
          f"{stats['objectives_new']} new ({stats['read'] - stats['objectives_new']} "
          f"reused), {stats['pooled']} added to the pool, {stats['coverage']} new "
          f"coverage edges into {ref!r}")
    if any(n for _, n in items) and not known:
        print("  note: no nodes loaded for that hierarchy yet -- run load_nodes.py "
              "to enable coverage checks")
    elif dangling:
        print(f"  warning: {len(dangling)} node_id(s) not found in {ref!r}: "
              f"{', '.join(dangling)}")


if __name__ == "__main__":
    main()
