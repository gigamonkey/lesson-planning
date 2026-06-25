"""Load a hierarchy markdown file into the lesson-planning `nodes` table.

Parses a curriculum-hierarchy markdown file directly (via the in-repo
`hierarchy.py`, which this repo now owns -- see `FORMAT.md`) into a flat,
already-tagged node list, then flattens that into one uniform table. Each node
carries its declared level `tag` (from the markdown's required `levels:` front
matter), structural `parent`, leaf flag, sibling ordinal, and text, so the app
can run gap/coverage queries without caring about the level structure. No
curriculum-flavor knowledge lives here anymore: kind comes from the markdown's
required `kind:`, and the slug/course default from the input filename and flags.

    nodes(hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)

`node_id` is the verbatim id (e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'); `level` is
the node's tag string ('unit', 'topic', 'essential-knowledge', ...); `is_leaf`
marks nodes with no children (the unit of "coverage"); `ordinal` is document
order. Keyed by hierarchy slug: re-running replaces only that hierarchy's rows so
several hierarchies can share one database. The hierarchy is registered in
`hierarchies` (editable=0, of a kind/type like 'ced' or 'syllabus') and its
`course` is upserted into `courses`. The slug defaults to the input filename
stem, the course to the slug; both (and the kind/title) take CLI overrides.

    uv run load_nodes.py my-course-hierarchy.md db.db
    uv run load_nodes.py another-hierarchy.md db.db --course mycourse

The title and kind come from the markdown's `---` front matter; `kind:` is
required, `title:` optional.
"""

import argparse
import os
import sqlite3

import hierarchy

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def apply_schema(conn):
    """Create every table from the canonical schema (idempotent)."""
    conn.executescript(open(SCHEMA_PATH).read())

# Major version of the hierarchy-document format this loader understands (the dict
# hierarchy.to_nodes emits). Semver; only the major is checked. See FORMAT.md.
FORMAT_MAJOR = 2

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

    Returns {version, slug, title, kind, levels, nodes:[...]} -- the same shape
    `build_rows`/`load` consume. slug/title/kind come from the front matter
    (`slug:` bare id optional, `title:` required, `kind:` optional provenance).
    Raises SystemExit on unparseable markdown or missing required front matter
    (propagated from hierarchy.py).
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


def build_rows(course, hierarchy, nodes):
    """Map a parsed hierarchy document's `nodes` to `nodes`-table rows, in order.

    Returns (course, hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)
    rows. The node list already carries each node's tag (-> level), parent, leaf
    flag, and text; `ordinal` here is the node's 0-based position in document order
    (the list is pre-order DFS), NOT the document's per-parent `ordinal`.
    """
    return [
        (course, hierarchy, n["id"], n["parent"], n["tag"], int(n["is_leaf"]), i, n["text"])
        for i, n in enumerate(nodes)
    ]


def build_durations(course, hierarchy, nodes):
    """`node_duration` rows for the nodes that carry a duration tag:
    (course, hierarchy, node_id, amount, unit)."""
    return [
        (course, hierarchy, n["id"], n["duration"]["amount"], n["duration"]["unit"])
        for n in nodes if n.get("duration")
    ]


def load_into(conn, slug, course, kind, course_title, rows, source=None, title=None,
              durations=None, source_md=None):
    """Replace one reference hierarchy's nodes + register it, on a caller's conn.

    Assumes the schema is already applied (the caller -- plan_io / `load` --
    applies it). `rows`/`durations` carry (course, hierarchy, ...) keys. The
    hierarchy's nodes and durations are cleared and replaced. `source_md` is the
    verbatim source markdown, stored so write_course can replay it. `kind` may be
    None (optional provenance). Does not commit (the caller owns the transaction).
    """
    conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)"
                 " ON CONFLICT(course) DO NOTHING",
                 (course, course_title))
    conn.execute("DELETE FROM nodes WHERE course=? AND hierarchy=?", (course, slug))
    conn.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute("DELETE FROM node_duration WHERE course=? AND hierarchy=?", (course, slug))
    conn.executemany("INSERT INTO node_duration VALUES (?, ?, ?, ?, ?)", durations or [])
    if not title:   # title is required in the markdown; this is a last-ditch default
        title = hierarchy_title(course, kind) if kind else slug
    conn.execute(
        "INSERT INTO hierarchies(course, hierarchy, kind, editable, title, source, source_md)"
        " VALUES (?, ?, ?, 0, ?, ?, ?)"
        " ON CONFLICT(course, hierarchy) DO UPDATE SET kind=excluded.kind,"
        " editable=0, title=excluded.title, source=excluded.source,"
        " source_md=excluded.source_md",
        (course, slug, kind, title, source, source_md))


def load(db_path, slug, course, kind, course_title, rows, source=None, title=None,
         durations=None, source_md=None):
    """Replace one reference hierarchy's nodes and register its course/hierarchy.

    `rows` carry `slug` as their hierarchy column. The course is created if new but
    its title is NOT changed by loading a hierarchy (a course is named when it's
    created). The hierarchy is registered as a reference (editable=0) of the given
    kind; its display title is `title` if given, else derived from course+kind.
    `durations` are node_duration rows for this hierarchy (replaced wholesale).
    """
    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn)
        load_into(conn, slug, course, kind, course_title, rows, source, title, durations,
                  source_md)
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="hierarchy markdown file")
    parser.add_argument("database", help="SQLite database file")
    parser.add_argument("--hierarchy", help="hierarchy slug (default: input filename stem)")
    parser.add_argument("--course", help="course id (default: the slug)")
    parser.add_argument("--kind", help="hierarchy kind/type (default: front-matter kind:)")
    parser.add_argument("--course-title", dest="course_title",
                        help="course title (default: course id, upper-cased)")
    args = parser.parse_args()

    with open(args.input) as f:
        text = f.read()
    doc = parse(text)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    slug = args.hierarchy or doc.get("slug") or stem
    course = args.course or slug
    kind = args.kind or doc.get("kind")
    course_title = args.course_title or course.upper()
    rows = build_rows(course, slug, doc["nodes"])
    durations = build_durations(course, slug, doc["nodes"])
    load(args.database, slug, course, kind, course_title,
         rows, source=args.input, title=doc.get("title"), durations=durations,
         source_md=text)

    leaves = sum(1 for r in rows if r[5])
    print(
        f"loaded {len(rows)} nodes for hierarchy {slug!r} "
        f"(course {course!r}, kind {kind!r}, {leaves} leaves) into {args.database}"
    )


if __name__ == "__main__":
    main()
