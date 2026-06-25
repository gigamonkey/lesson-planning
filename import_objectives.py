"""Import raw objectives into a course's pool, interning by text.

Two input shapes, auto-detected from whether the first non-blank line has a tab:

  * Plain text -- one objective per non-blank line (no tabs, no header). Each
    line becomes a raw objective in the course's pool, with no coverage.

  * TSV table -- a header row naming columns. `objective` is required (`text` is
    accepted as an alias). The coverage target is the `hierarchy_id`/`hierarchy`
    column (the bare, course-relative slug -- the course is known from --course)
    plus a `node_id`/`ek` column; blank or 'none' = pool only. `uuid` is optional.
    A node_id implies its ancestors, so only the node itself is needed.

An optional `uuid` column lets a row name an existing objective: a known uuid
identifies it directly (preserving identity even if the text was edited),
otherwise the objective is interned by exact text. So the round-trip works --
download the uuid+text TSV from the app, add a `node_id` column via some
classification step, and reimport to attach those placements. The import is
idempotent: re-running adds no duplicate objectives, pool memberships, or
coverage edges (already-present node_id assignments are no-ops), while new
node_ids for an existing objective are added. uuids are generated when absent.

There is no default coverage target: a row's hierarchy comes from its
`hierarchy_id` column, or from `--hierarchy` (a per-hierarchy import), else the
row is pool-only. node_ids are checked against the loaded `nodes` (load_nodes.py
first) and unknown ones are reported -- still inserted, but flag a mislabeled
objective or a change.

    uv run import_objectives.py objectives.txt db.db --course csa
    uv run import_objectives.py categorized.tsv db.db --course csa
"""

import argparse
import csv
import io
import os
import sqlite3
import uuid as uuidlib

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def apply_schema(conn):
    """Create every table from the canonical schema (idempotent)."""
    conn.executescript(open(SCHEMA_PATH).read())


def parse_coverage(content, default_hierarchy=None):
    """Parse upload CONTENT into (rows, mode) where each row is
    (uuid|None, text, hierarchy|None, node_id|None).

    Plain text (no tab) -> one objective per line, no placement. A TSV uses its
    header: `objective`/`text` (required), optional `uuid`, optional `node_id`/`ek`,
    optional `hierarchy_id`/`hierarchy`. A row's hierarchy falls back to
    `default_hierarchy` (so a per-hierarchy upload omits the column)."""
    lines = content.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    if "\t" not in first:
        return [(None, ln.strip(), None, None) for ln in lines if ln.strip()], "text"

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    cols = reader.fieldnames or []
    text_col = "objective" if "objective" in cols else "text" if "text" in cols else None
    if not text_col:
        raise ValueError(
            f"table input must have an 'objective' (or 'text') column; got: {', '.join(cols)}")
    node_col = next((c for c in ("node_id", "ek") if c in cols), None)
    hier_col = next((c for c in ("hierarchy_id", "hierarchy") if c in cols), None)
    rows = []
    for r in reader:
        text = (r.get(text_col) or "").strip()
        if not text:
            continue
        uuid = (r.get("uuid") or "").strip() or None
        node = (r.get(node_col) or "").strip() if node_col else ""
        node = None if node.lower() in ("", "none") else node
        hier = ((r.get(hier_col) or "").strip() if hier_col else "") or default_hierarchy
        rows.append((uuid, text, hier, node))
    return rows, "table"


def _resolve_upsert(conn, uuid_in, text, stats):
    """uuid for (uuid_in, text), updating identity-keyed text. A known uuid keeps
    its identity and its text is REPLACED by `text` (unless that text already
    belongs to another objective -- UNIQUE(text) -- in which case it's left and
    counted as a conflict). Otherwise intern by text, else create."""
    if uuid_in:
        row = conn.execute("SELECT text FROM objectives WHERE uuid=?", (uuid_in,)).fetchone()
        if row:
            if row[0] != text:
                clash = conn.execute("SELECT 1 FROM objectives WHERE text=? AND uuid<>?",
                                     (text, uuid_in)).fetchone()
                if clash:
                    stats["text_conflicts"] += 1
                else:
                    conn.execute("UPDATE objectives SET text=? WHERE uuid=?", (text, uuid_in))
                    stats["text_updated"] += 1
            return uuid_in
    row = conn.execute("SELECT uuid FROM objectives WHERE text=?", (text,)).fetchone()
    if row:
        return row[0]
    u = uuid_in or str(uuidlib.uuid4())
    conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (u, text))
    stats["objectives_new"] += 1
    return u


def upsert(db_path, course, rows):
    """Authoritative import of (uuid|None, text, hierarchy|None, node_id|None) rows.

    Identity is the uuid: a known uuid keeps its identity and its text is replaced
    by the row's (see _resolve_upsert); no uuid interns by text. Every objective is
    ensured in the course pool. For each (hierarchy, uuid) named with valid node(s)
    in this upload, the objective's placement in that hierarchy is REPLACED by those
    node(s) (existing coverage there is cleared first). Unknown node_ids are
    reported and leave the prior placement untouched.

    Returns (stats, dangling) -- dangling maps hierarchy -> sorted unknown node_ids.
    """
    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn)
        stats = {"read": 0, "objectives_new": 0, "text_updated": 0, "text_conflicts": 0,
                 "pooled": 0, "placed": 0}
        pos = conn.execute("SELECT COALESCE(MAX(position), -1)+1 FROM course_objectives"
                           " WHERE course=?", (course,)).fetchone()[0]
        known_cache, dangling, placements = {}, {}, {}
        for uuid_in, text, hierarchy, node in rows:
            stats["read"] += 1
            uuid = _resolve_upsert(conn, uuid_in, text, stats)
            if not conn.execute("SELECT 1 FROM course_objectives WHERE course=? AND uuid=?",
                                (course, uuid)).fetchone():
                conn.execute("INSERT INTO course_objectives(course, uuid, position)"
                             " VALUES (?, ?, ?)", (course, uuid, pos))
                pos += 1
                stats["pooled"] += 1
            if hierarchy and node:
                if hierarchy not in known_cache:
                    known_cache[hierarchy] = {n for (n,) in conn.execute(
                        "SELECT node_id FROM nodes WHERE course=? AND hierarchy=?", (course, hierarchy))}
                if known_cache[hierarchy] and node not in known_cache[hierarchy]:
                    dangling.setdefault(hierarchy, set()).add(node)
                else:
                    placements.setdefault((hierarchy, uuid), set()).add(node)
        # Replace placement: only for (hierarchy, uuid) named with valid nodes here.
        # New edges append after the node's existing objectives (coverage.position).
        for (hierarchy, uuid), nodes in placements.items():
            conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy=? AND uuid=?",
                         (course, hierarchy, uuid))
            for node in nodes:
                nxt = conn.execute("SELECT COALESCE(MAX(position), -1)+1 FROM coverage"
                                   " WHERE course=? AND hierarchy=? AND node_id=?",
                                   (course, hierarchy, node)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position)"
                             " VALUES (?, ?, ?, ?, ?)", (course, hierarchy, uuid, node, nxt))
                stats["placed"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats, {h: sorted(s) for h, s in dangling.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="objectives file (plain text or TSV table)")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--course", default="csa", help="course id (default: csa)")
    parser.add_argument("--hierarchy",
                        help="coverage target for rows without a hierarchy_id column "
                             "(omit for a pool-only import or a TSV that names its own)")
    parser.add_argument("--replace", action="store_true",
                        help="clear the course's pool and its reference coverage before "
                             "importing (the outline's placements are left intact)")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        content = f.read()
    try:
        rows, mode = parse_coverage(content, default_hierarchy=args.hierarchy)
    except ValueError as e:
        raise SystemExit(str(e))

    if args.replace:
        conn = sqlite3.connect(args.database)
        try:
            apply_schema(conn)
            conn.execute("DELETE FROM coverage WHERE course=? AND hierarchy IN "
                         "(SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0)",
                         (args.course, args.course))
            conn.execute("DELETE FROM course_objectives WHERE course=?", (args.course,))
            conn.commit()
        finally:
            conn.close()

    stats, dangling = upsert(args.database, args.course, rows)
    print(f"{mode}: read {stats['read']} objectives for course {args.course!r} -> "
          f"{stats['objectives_new']} new, {stats['pooled']} added to the pool, "
          f"{stats['placed']} placement(s)")
    for hier, ids in dangling.items():
        print(f"  warning: {len(ids)} node_id(s) not found in {hier!r}: {', '.join(ids)}")


if __name__ == "__main__":
    main()
