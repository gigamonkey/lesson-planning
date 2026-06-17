"""Normalize a hierarchy markdown file into the lesson-planning `nodes` table.

Reads a CSA/CSP/IB (or book) hierarchy markdown file -- the same input
build_hierarchy_db.py and build_hierarchy_xml.py consume -- and flattens it into
one uniform, course-agnostic table so the lesson-planning app can run
gap/coverage queries without caring about the per-flavor level structure:

    nodes(course, node_id, parent_id, level, is_leaf, ordinal, text)

`node_id` is the verbatim id (e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'); `level` is
the flavor's level tag ('unit', 'topic', 'essential-knowledge', ...); `is_leaf`
marks nodes with no children (the unit of "coverage"); `ordinal` is document
order. The table is course-scoped: re-running replaces only the loaded course's
rows so several courses can share one database.

    uv run load_nodes.py csa/ced-2025-hierarchy.md lesson-planning/db.db
    uv run load_nodes.py ib/ib-hierarchy.md lesson-planning/db.db --course ib
"""

import argparse
import sqlite3

from hierarchy import LEVEL_TAGS, parse_sections

DDL = """
CREATE TABLE IF NOT EXISTS nodes (
  course    TEXT    NOT NULL,
  node_id   TEXT    NOT NULL,
  parent_id TEXT,
  level     TEXT    NOT NULL,
  is_leaf   INTEGER NOT NULL,
  ordinal   INTEGER NOT NULL,
  text      TEXT    NOT NULL,
  PRIMARY KEY (course, node_id)
)
"""


def section_text(sec):
    """Join a section's heading text and body, trimming surrounding blanks."""
    lines = ([sec["head"]] if sec["head"] else []) + sec["body"]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_rows(course, flavor, sections):
    """Return (course, node_id, parent_id, level, is_leaf, ordinal, text) rows.

    parent_id is the most recent ancestor at a shallower level; is_leaf is true
    when the next section in document order is not deeper (a node's children, if
    any, immediately follow it in a depth-first markdown hierarchy).
    """
    tags = LEVEL_TAGS[flavor]
    rows = []
    ancestor = {}  # level -> id of the current node at that level
    for i, sec in enumerate(sections):
        level = sec["level"]
        ancestor[level] = sec["id"]
        for deeper in [lvl for lvl in ancestor if lvl > level]:
            del ancestor[deeper]
        parent_id = next(
            (ancestor[lvl] for lvl in range(level - 1, 0, -1) if lvl in ancestor),
            None,
        )
        is_leaf = i + 1 >= len(sections) or sections[i + 1]["level"] <= level
        rows.append(
            (course, sec["id"], parent_id, tags[level], int(is_leaf), i,
             section_text(sec))
        )
    return rows


def load(db_path, course, rows):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(DDL)
        conn.execute("DELETE FROM nodes WHERE course = ?", (course,))
        conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="hierarchy markdown file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument(
        "--course",
        help="course id for these nodes (default: the detected flavor)",
    )
    args = parser.parse_args()

    with open(args.input) as f:
        flavor, sections = parse_sections(f.read())
    course = args.course or flavor
    rows = build_rows(course, flavor, sections)
    load(args.database, course, rows)

    leaves = sum(r[4] for r in rows)
    print(
        f"{flavor}: loaded {len(rows)} nodes for course {course!r} "
        f"({leaves} leaves) into {args.database}"
    )


if __name__ == "__main__":
    main()
