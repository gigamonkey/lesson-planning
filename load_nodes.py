"""Normalize a hierarchy markdown file into the lesson-planning `nodes` table.

Reads a hierarchy markdown file (any recognized flavor -- see hierarchy.py) and
flattens it into one uniform table so the lesson-planning app can run gap/coverage
queries without caring about the per-flavor level structure:

    nodes(hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)

`node_id` is the verbatim id (e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'); `level` is
the flavor's level tag ('unit', 'topic', 'essential-knowledge', ...); `is_leaf`
marks nodes with no children (the unit of "coverage"); `ordinal` is document
order. Keyed by hierarchy slug: re-running replaces only that hierarchy's rows so
several hierarchies can share one database. The hierarchy is registered in
`hierarchies` (editable=0, of a kind/type like 'ced' or 'ib-syllabus') and its
`course` is upserted into `courses` -- the slug/course/kind default from the
detected flavor and can be overridden with the matching flags.

    uv run load_nodes.py my-course-hierarchy.md db.db
    uv run load_nodes.py another-hierarchy.md db.db --course mycourse
"""

import argparse
import sqlite3

from hierarchy import LEVEL_TAGS, hierarchy_title, parse_sections

DDL = """
CREATE TABLE IF NOT EXISTS nodes (
  hierarchy TEXT    NOT NULL,
  node_id   TEXT    NOT NULL,
  parent_id TEXT,
  level     TEXT    NOT NULL,
  is_leaf   INTEGER NOT NULL,
  ordinal   INTEGER NOT NULL,
  text      TEXT    NOT NULL,
  PRIMARY KEY (hierarchy, node_id)
)
"""

COURSES_DDL = ("CREATE TABLE IF NOT EXISTS courses (course TEXT PRIMARY KEY,"
               " title TEXT NOT NULL)")
HIERARCHIES_DDL = ("CREATE TABLE IF NOT EXISTS hierarchies (hierarchy TEXT PRIMARY KEY,"
                   " course TEXT NOT NULL, kind TEXT NOT NULL, editable INTEGER NOT NULL,"
                   " title TEXT NOT NULL, source TEXT)")

# Per-flavor defaults for the course, reference kind (the TYPE), hierarchy slug,
# and course title. The slug is an opaque-but-readable handle; CLI flags override.
FLAVOR_META = {
    "csa":  {"course": "csa",  "kind": "ced",         "slug": "csa-ced",
             "course_title": "AP Computer Science A"},
    "csp":  {"course": "csp",  "kind": "ced",         "slug": "csp-ced",
             "course_title": "AP Computer Science Principles"},
    "ib":   {"course": "ib",   "kind": "ib-syllabus", "slug": "ib-syllabus",
             "course_title": "IB Computer Science"},
    "book": {"course": "book", "kind": "book",        "slug": "book",
             "course_title": "Book"},
}


def meta_for(flavor, course=None, kind=None, slug=None, course_title=None):
    """Resolve (course, kind, slug, course_title) for a flavor, with overrides."""
    m = dict(FLAVOR_META.get(flavor, {"course": flavor, "kind": flavor,
                                       "slug": flavor, "course_title": flavor.upper()}))
    for key, val in (("course", course), ("kind", kind), ("slug", slug),
                     ("course_title", course_title)):
        if val:
            m[key] = val
    return m


def section_text(sec):
    """Join a section's heading text and body, trimming surrounding blanks."""
    lines = ([sec["head"]] if sec["head"] else []) + sec["body"]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_rows(hierarchy, flavor, sections):
    """Return (hierarchy, node_id, parent_id, level, is_leaf, ordinal, text) rows.

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
            (hierarchy, sec["id"], parent_id, tags[level], int(is_leaf), i,
             section_text(sec))
        )
    return rows


def load(db_path, slug, course, kind, course_title, rows, source=None):
    """Replace one reference hierarchy's nodes and register its course/hierarchy.

    `rows` carry `slug` as their hierarchy column. The course is upserted, and the
    hierarchy is registered as a reference (editable=0) of the given kind (type).
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(COURSES_DDL)
        conn.execute(HIERARCHIES_DDL)
        conn.execute(DDL)
        conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)"
                     " ON CONFLICT(course) DO UPDATE SET title=excluded.title",
                     (course, course_title))
        conn.execute("DELETE FROM nodes WHERE hierarchy = ?", (slug,))
        conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        title = hierarchy_title(course, kind)
        conn.execute(
            "INSERT INTO hierarchies(hierarchy, course, kind, editable, title, source)"
            " VALUES (?, ?, ?, 0, ?, ?)"
            " ON CONFLICT(hierarchy) DO UPDATE SET course=excluded.course, kind=excluded.kind,"
            " editable=0, title=excluded.title, source=excluded.source",
            (slug, course, kind, title, source))
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="hierarchy markdown file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--hierarchy", help="hierarchy slug (default: derived from flavor)")
    parser.add_argument("--course", help="course id (default: derived from flavor)")
    parser.add_argument("--kind", help="hierarchy kind/type (default: derived from flavor)")
    parser.add_argument("--course-title", dest="course_title",
                        help="course title (default: derived from flavor)")
    args = parser.parse_args()

    with open(args.input) as f:
        flavor, sections = parse_sections(f.read())
    m = meta_for(flavor, args.course, args.kind, args.hierarchy, args.course_title)
    rows = build_rows(m["slug"], flavor, sections)
    load(args.database, m["slug"], m["course"], m["kind"], m["course_title"],
         rows, source=args.input)

    leaves = sum(r[4] for r in rows)
    print(
        f"{flavor}: loaded {len(rows)} nodes for hierarchy {m['slug']!r} "
        f"(course {m['course']!r}, kind {m['kind']!r}, {leaves} leaves) into {args.database}"
    )


if __name__ == "__main__":
    main()
