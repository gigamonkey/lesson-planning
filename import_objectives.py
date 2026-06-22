"""Import raw objectives into a course's pool, interning by text.

Two input shapes, auto-detected from whether the first non-blank line has a tab:

  * Plain text -- one objective per non-blank line (no tabs, no header). Each
    line becomes a raw objective in the course's pool, with no coverage.

  * TSV table -- a header row naming columns. `objective` is required (`text` is
    accepted as an alias). The coverage-target column is `node_id`/`ek` if present,
    otherwise the single column left over after uuid/objective/text (so a
    downloaded uuid/text file plus any added id column -- 'subsection', etc. --
    just works); blank or 'none' = pool only. `uuid` is optional. A node_id implies
    its ancestors, so only the node itself is needed.

An optional `uuid` column lets a row name an existing objective: a known uuid
identifies it directly (preserving identity even if the text was edited),
otherwise the objective is interned by exact text. So the round-trip works --
download the uuid+text TSV from the app, add a `node_id` column via some
classification step, and reimport to attach those placements. The import is
idempotent: re-running adds no duplicate objectives, pool memberships, or
coverage edges (already-present node_id assignments are no-ops), while new
node_ids for an existing objective are added. uuids are generated when absent.

Coverage edges go into the course's reference hierarchy (the CED, resolved from
`hierarchies`, default '<course>-ced'); --hierarchy targets another (IB, a book).
node_ids are checked against the loaded `nodes` (load_nodes.py first) and unknown
ones are reported -- still inserted, but flag a mislabeled objective or a change.

    uv run import_objectives.py objectives.txt db.db --course csa
    uv run import_objectives.py categorized.tsv db.db --course csa
"""

import argparse
import csv
import io
import sqlite3
import uuid as uuidlib

DDL = [
    """CREATE TABLE IF NOT EXISTS objectives (
         uuid TEXT PRIMARY KEY,
         text TEXT NOT NULL UNIQUE,
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


def parse_text(content):
    """Parse file CONTENT into (items, mode).

    items is a list of (uuid|None, text, node_id|None); mode is 'text' or 'table'.
    Raises ValueError on a table without an objective/text column.
    """
    lines = content.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")

    if "\t" not in first:  # plain text: one objective per non-blank line
        return [(None, ln.strip(), None) for ln in lines if ln.strip()], "text"

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    cols = reader.fieldnames or []
    text_col = "objective" if "objective" in cols else "text" if "text" in cols else None
    if not text_col:
        raise ValueError(
            f"table input must have an 'objective' (or 'text') column; got: {', '.join(cols)}")
    # The node-id column: a recognized name, else the single leftover column (so a
    # downloaded uuid/text file + any added id column -- 'subsection', 'ek', ... --
    # just works). Ambiguous (>1 leftover, none recognized) -> no coverage.
    node_col = next((c for c in ("node_id", "ek") if c in cols), None)
    if not node_col:
        leftover = [c for c in cols if c not in ("uuid", "objective", "text")]
        node_col = leftover[0] if len(leftover) == 1 else None
    items = []
    for r in reader:
        text = (r.get(text_col) or "").strip()
        if not text:
            continue
        uuid = (r.get("uuid") or "").strip() or None
        node = (r.get(node_col) or "").strip() if node_col else ""
        items.append((uuid, text, None if node.lower() in ("", "none") else node))
    return items, "table"


def parse_items(path):
    """parse_text() over a file path."""
    with open(path, encoding="utf-8") as f:
        return parse_text(f.read())


def reference_slug(conn, course):
    """The course's primary reference hierarchy slug; load_nodes registers it.

    Prefers the course's explicit `primary_reference`, else the first reference
    (ced-ordered), else the conventional '<course>-ced' if none is loaded yet (the
    coverage edges still record where the objectives belong).
    """
    try:
        row = conn.execute("SELECT primary_reference FROM courses WHERE course=?",
                           (course,)).fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass
    try:
        row = conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0 "
            "ORDER BY (kind='ced') DESC, hierarchy LIMIT 1", (course,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return row[0] if row else f"{course}-ced"


def resolve_uuid(conn, uuid, text, stats):
    """The uuid to use for (uuid, text), creating the objective if needed.

    A given uuid that already exists wins (preserves the objective's identity on
    reimport, regardless of edits to its text); otherwise intern by text; failing
    that create a new objective (with the given uuid if one was supplied)."""
    if uuid and conn.execute("SELECT 1 FROM objectives WHERE uuid=?", (uuid,)).fetchone():
        return uuid
    row = conn.execute("SELECT uuid FROM objectives WHERE text=?", (text,)).fetchone()
    if row:
        return row[0]
    u = uuid or str(uuidlib.uuid4())
    conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (u, text))
    stats["objectives_new"] += 1
    return u


def load(db_path, course, items, hierarchy=None, replace=False):
    """Import `items` (uuid|None, text, node_id|None) into the course's pool and
    coverage. uuid is optional: when given and known it identifies the objective
    (else interned by text). Idempotent -- re-running adds nothing already present.

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

        for uuid_in, text, node in items:
            stats["read"] += 1
            uuid = resolve_uuid(conn, uuid_in, text, stats)
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

    try:
        items, mode = parse_items(args.input)
    except ValueError as e:
        raise SystemExit(str(e))
    ref, stats, dangling, known = load(args.database, args.course, items,
                                       hierarchy=args.hierarchy, replace=args.replace)
    print(f"{mode}: read {stats['read']} objectives for course {args.course!r} -> "
          f"{stats['objectives_new']} new ({stats['read'] - stats['objectives_new']} "
          f"reused), {stats['pooled']} added to the pool, {stats['coverage']} new "
          f"coverage edges into {ref!r}")
    if any(node for _, _, node in items) and not known:
        print("  note: no nodes loaded for that hierarchy yet -- run load_nodes.py "
              "to enable coverage checks")
    elif dangling:
        print(f"  warning: {len(dangling)} node_id(s) not found in {ref!r}: "
              f"{', '.join(dangling)}")


if __name__ == "__main__":
    main()
