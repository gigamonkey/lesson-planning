"""Load a hierarchy node-list JSON file into the lesson-planning `nodes` table.

Consumes the node-list JSON emitted by the hierarchy-extractors repo's
`build_hierarchy_json.py` -- the cross-repo data contract (see that repo's
`json-format.md`). Each node already carries its resolved level `tag`, structural
`parent`, leaf flag, sibling ordinal, and text, so this loader does **no markdown
parsing** and needs no curriculum-flavor knowledge beyond mapping the `flavor`
string to local course/kind/slug policy (`FLAVOR_META`). It flattens the document
into one uniform table so the app can run gap/coverage queries without caring
about the per-flavor level structure:

    nodes(hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)

`node_id` is the verbatim id (e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'); `level` is
the node's tag string ('unit', 'topic', 'essential-knowledge', ...); `is_leaf`
marks nodes with no children (the unit of "coverage"); `ordinal` is document
order. Keyed by hierarchy slug: re-running replaces only that hierarchy's rows so
several hierarchies can share one database. The hierarchy is registered in
`hierarchies` (editable=0, of a kind/type like 'ced' or 'syllabus') and its
`course` is upserted into `courses` -- the slug/course/kind default from the
document's flavor and can be overridden with the matching flags.

    uv run load_nodes.py my-course-hierarchy.json db.db
    uv run load_nodes.py another-hierarchy.json db.db --course mycourse

Author hierarchies as markdown, then run `build_hierarchy_json.py` (in the
hierarchy-extractors repo) to produce the JSON this loads.
"""

import argparse
import json
import sqlite3

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

# Major version of the node-list JSON format this loader understands. The format
# is semver; a consumer checks only the major (see json-format.md "Versioning").
FORMAT_MAJOR = 1

# Per-flavor defaults for the course, reference kind (the TYPE), hierarchy slug,
# and course title. The slug is an opaque-but-readable handle; CLI flags override.
# This is consumer-side app policy -- the JSON contract carries only `flavor`.
FLAVOR_META = {
    "csa":  {"course": "csa",  "kind": "ced",         "slug": "csa-ced",
             "course_title": "AP Computer Science A"},
    "csp":  {"course": "csp",  "kind": "ced",         "slug": "csp-ced",
             "course_title": "AP Computer Science Principles"},
    "ib":   {"course": "ib",   "kind": "syllabus",    "slug": "ib-syllabus",
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


def kind_label(course, kind):
    """Short, clean label for a hierarchy's kind. Drops a redundant leading course
    id (legacy kind 'ib-syllabus' -> 'syllabus') then tidies ('ced' -> 'CED',
    'course-outline' -> 'course outline', dashes -> spaces)."""
    parts = kind.split("-")
    if parts[0] == course:
        parts = parts[1:]
    k = "-".join(parts)
    return {"ced": "CED", "course-outline": "course outline"}.get(k, k.replace("-", " "))


def hierarchy_title(course, kind):
    """Display title for a hierarchy, e.g. 'CSA CED', 'IB syllabus'."""
    return f"{course.upper()} {kind_label(course, kind)}"


def load_doc(doc):
    """Validate a node-list JSON document and return it.

    Checks only the format's MAJOR version (minor/patch are backward-compatible
    and unknown fields are ignored, per the contract). Raises ValueError on an
    unsupported major.
    """
    version = str(doc.get("version", ""))
    major = version.split(".")[0]
    if not major.isdigit() or int(major) != FORMAT_MAJOR:
        raise ValueError(
            f"unsupported node-list JSON version {version!r} "
            f"(this loader handles major {FORMAT_MAJOR})")
    return doc


def build_rows(hierarchy, nodes):
    """Map node-list JSON `nodes` to `nodes`-table rows, in document order.

    Returns (hierarchy, node_id, parent_id, level, is_leaf, ordinal, text) rows.
    The JSON already carries each node's tag (-> level), parent, leaf flag, and
    text; `ordinal` here is the node's 0-based position in document order (the
    array is pre-order DFS) -- what the table has always stored, NOT the JSON's
    per-parent `ordinal`.
    """
    return [
        (hierarchy, n["id"], n["parent"], n["tag"], int(n["is_leaf"]), i, n["text"])
        for i, n in enumerate(nodes)
    ]


def load(db_path, slug, course, kind, course_title, rows, source=None, title=None):
    """Replace one reference hierarchy's nodes and register its course/hierarchy.

    `rows` carry `slug` as their hierarchy column. The course is created if new but
    its title is NOT changed by loading a hierarchy (a course is named when it's
    created). The hierarchy is registered as a reference (editable=0) of the given
    kind; its display title is `title` if given, else derived from course+kind.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(COURSES_DDL)
        conn.execute(HIERARCHIES_DDL)
        conn.execute(DDL)
        conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)"
                     " ON CONFLICT(course) DO NOTHING",
                     (course, course_title))
        conn.execute("DELETE FROM nodes WHERE hierarchy = ?", (slug,))
        conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        title = title or hierarchy_title(course, kind)
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
    parser.add_argument("input", help="hierarchy node-list JSON file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--hierarchy", help="hierarchy slug (default: derived from flavor)")
    parser.add_argument("--course", help="course id (default: derived from flavor)")
    parser.add_argument("--kind", help="hierarchy kind/type (default: derived from flavor)")
    parser.add_argument("--course-title", dest="course_title",
                        help="course title (default: derived from flavor)")
    args = parser.parse_args()

    with open(args.input) as f:
        doc = load_doc(json.load(f))
    flavor = doc["flavor"]
    m = meta_for(flavor, args.course, args.kind or doc.get("kind"),
                 args.hierarchy, args.course_title)
    rows = build_rows(m["slug"], doc["nodes"])
    load(args.database, m["slug"], m["course"], m["kind"], m["course_title"],
         rows, source=args.input, title=doc.get("title"))

    leaves = sum(1 for r in rows if r[4])
    print(
        f"{flavor}: loaded {len(rows)} nodes for hierarchy {m['slug']!r} "
        f"(course {m['course']!r}, kind {m['kind']!r}, {leaves} leaves) into {args.database}"
    )


if __name__ == "__main__":
    main()
