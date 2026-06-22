"""Load a hierarchy markdown file into the lesson-planning `nodes` table.

Parses a curriculum-hierarchy markdown file directly (via the in-repo
`hierarchy.py`, which this repo now owns -- see `FORMAT.md`) into a flat,
already-tagged node list, then flattens that into one uniform table. Each node
carries its resolved level `tag`, structural `parent`, leaf flag, sibling
ordinal, and text, so the app can run gap/coverage queries without caring about
the per-flavor level structure; the only curriculum-flavor knowledge here is
mapping the detected `flavor` to local course/kind/slug policy (`FLAVOR_META`):

    nodes(hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)

`node_id` is the verbatim id (e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'); `level` is
the node's tag string ('unit', 'topic', 'essential-knowledge', ...); `is_leaf`
marks nodes with no children (the unit of "coverage"); `ordinal` is document
order. Keyed by hierarchy slug: re-running replaces only that hierarchy's rows so
several hierarchies can share one database. The hierarchy is registered in
`hierarchies` (editable=0, of a kind/type like 'ced' or 'syllabus') and its
`course` is upserted into `courses` -- the slug/course/kind default from the
document's flavor and can be overridden with the matching flags.

    uv run load_nodes.py my-course-hierarchy.md db.db
    uv run load_nodes.py another-hierarchy.md db.db --course mycourse

The title and kind come from the markdown's `---` front matter (`title:` /
`kind:`); both fall back to per-flavor defaults.
"""

import argparse
import sqlite3

import hierarchy

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
               " title TEXT NOT NULL, primary_reference TEXT, primary_outline TEXT)")
HIERARCHIES_DDL = ("CREATE TABLE IF NOT EXISTS hierarchies (hierarchy TEXT PRIMARY KEY,"
                   " course TEXT NOT NULL, kind TEXT NOT NULL, editable INTEGER NOT NULL,"
                   " title TEXT NOT NULL, source TEXT)")

# Major version of the hierarchy-document format this loader understands (the dict
# hierarchy.to_nodes emits). Semver; only the major is checked. See FORMAT.md.
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


def parse(text):
    """Parse hierarchy markdown into a node-list document (hierarchy.to_nodes).

    Returns {version, flavor, title, kind, levels, nodes:[...]} -- the same shape
    `build_rows`/`load` consume. Title and kind come from the markdown's front
    matter (with per-flavor kind fallback). Raises SystemExit on unparseable
    markdown (propagated from hierarchy.py).
    """
    return load_doc(hierarchy.to_nodes(text))


def load_doc(doc):
    """Validate a parsed hierarchy document and return it.

    Checks only the format's MAJOR version (minor/patch are backward-compatible
    and unknown fields are ignored). Raises ValueError on an unsupported major.
    """
    version = str(doc.get("version", ""))
    major = version.split(".")[0]
    if not major.isdigit() or int(major) != FORMAT_MAJOR:
        raise ValueError(
            f"unsupported hierarchy format version {version!r} "
            f"(this loader handles major {FORMAT_MAJOR})")
    return doc


def build_rows(hierarchy, nodes):
    """Map a parsed hierarchy document's `nodes` to `nodes`-table rows, in order.

    Returns (hierarchy, node_id, parent_id, level, is_leaf, ordinal, text) rows.
    The node list already carries each node's tag (-> level), parent, leaf flag,
    and text; `ordinal` here is the node's 0-based position in document order (the
    list is pre-order DFS) -- what the table has always stored, NOT the document's
    per-parent `ordinal`.
    """
    return [
        (hierarchy, n["id"], n["parent"], n["tag"], int(n["is_leaf"]), i, n["text"])
        for i, n in enumerate(nodes)
    ]


def load_into(conn, slug, course, kind, course_title, rows, source=None, title=None):
    """Replace one reference hierarchy's nodes + register it, on a caller's conn.

    Does not commit (the caller owns the transaction). See `load` for the
    self-contained, db_path-taking wrapper.
    """
    conn.execute(COURSES_DDL)
    conn.execute(HIERARCHIES_DDL)
    conn.execute(DDL)
    conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)"
                 " ON CONFLICT(course) DO NOTHING",
                 (course, course_title))
    conn.execute("DELETE FROM nodes WHERE hierarchy = ?", (slug,))
    conn.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    title = title or hierarchy_title(course, kind)
    conn.execute(
        "INSERT INTO hierarchies(hierarchy, course, kind, editable, title, source)"
        " VALUES (?, ?, ?, 0, ?, ?)"
        " ON CONFLICT(hierarchy) DO UPDATE SET course=excluded.course, kind=excluded.kind,"
        " editable=0, title=excluded.title, source=excluded.source",
        (slug, course, kind, title, source))


def load(db_path, slug, course, kind, course_title, rows, source=None, title=None):
    """Replace one reference hierarchy's nodes and register its course/hierarchy.

    `rows` carry `slug` as their hierarchy column. The course is created if new but
    its title is NOT changed by loading a hierarchy (a course is named when it's
    created). The hierarchy is registered as a reference (editable=0) of the given
    kind; its display title is `title` if given, else derived from course+kind.
    """
    conn = sqlite3.connect(db_path)
    try:
        load_into(conn, slug, course, kind, course_title, rows, source, title)
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="hierarchy markdown file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--hierarchy", help="hierarchy slug (default: derived from flavor)")
    parser.add_argument("--course", help="course id (default: derived from flavor)")
    parser.add_argument("--kind", help="hierarchy kind/type (default: front matter / flavor)")
    parser.add_argument("--course-title", dest="course_title",
                        help="course title (default: derived from flavor)")
    args = parser.parse_args()

    with open(args.input) as f:
        doc = parse(f.read())
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
