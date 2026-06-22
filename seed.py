"""Seed a database from a directory of input files described by a manifest.

Reads <seed_dir>/manifest.toml and, for each course that does NOT already exist,
creates it, loads its hierarchies (node-list JSON), and imports its objectives
(pool-only, or categorized into a named hierarchy). Idempotent at course
granularity: an already-present course is left untouched, so this is safe to run
on every startup. It supplies the policy the input files don't carry (which course
a hierarchy/objectives file belongs to, and which hierarchy a node-id column
indexes) -- see plans/seed-on-startup.md.

Manifest (TOML; paths are relative to the manifest):

    [[course]]
    id = "csa"
    title = "AP Computer Science A"        # optional; default id.upper()

      [[course.hierarchy]]
      file = "csa-ced.json"                # node-list JSON (flavor read from file)
      kind = "ced"                         # optional; default from flavor
      slug = "csa-ced"                     # optional; default <id>-<kind>
      title = "CSA CED"                    # optional; default derived

      [[course.objectives]]
      file = "csa-objectives.txt"          # pool-only (no `hierarchy`)

      [[course.objectives]]
      file = "csa-ced-coverage.tsv"
      hierarchy = "csa-ced"                # categorize node_ids into this hierarchy

Run as a CLI too (applies schema.sql to a fresh db first):

    uv run seed.py <seed-dir> [db.db]
"""

import argparse
import json
import os
import sqlite3
import sys
import tomllib

import import_objectives
import load_nodes

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def _ensure_outline(conn, course):
    """Create the course's outline hierarchy if absent (mirrors app.ensure_outline,
    kept here so seed.py stands alone). Returns the outline slug."""
    row = conn.execute(
        "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 "
        "ORDER BY (kind='course-outline') DESC, hierarchy LIMIT 1", (course,)).fetchone()
    if row:
        return row[0]
    outline = course + "-plan"
    conn.execute(
        "INSERT OR IGNORE INTO hierarchies(hierarchy, course, kind, editable, title, source)"
        " VALUES (?, ?, 'course-outline', 1, 'Course outline', NULL)", (outline, course))
    return outline


def _load_hierarchy(db_path, course, spec, seed_dir):
    """Load one node-list JSON as a reference of `course`. Returns (slug, n_nodes)."""
    with open(os.path.join(seed_dir, spec["file"])) as f:
        doc = load_nodes.load_doc(json.load(f))
    kind = spec.get("kind") or load_nodes.meta_for(doc["flavor"])["kind"]
    slug = spec.get("slug") or f"{course}-{kind}"
    rows = load_nodes.build_rows(slug, doc["nodes"])
    conn = sqlite3.connect(db_path)
    try:
        course_title = conn.execute(
            "SELECT title FROM courses WHERE course=?", (course,)).fetchone()[0]
        out = conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 LIMIT 1",
            (course,)).fetchone()
    finally:
        conn.close()
    load_nodes.load(db_path, slug, course, kind, course_title, rows,
                    source=spec["file"], title=spec.get("title"))
    if out:  # measure the outline against this reference
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                         " VALUES (?, ?)", (out[0], slug))
            conn.commit()
        finally:
            conn.close()
    return slug, len(rows)


def _load_objectives(db_path, course, spec, seed_dir):
    """Import one objectives file. With `hierarchy`, categorize node_ids into it
    (unknown ids dropped, like the hierarchy upload); otherwise pool-only."""
    items, _mode = import_objectives.parse_items(os.path.join(seed_dir, spec["file"]))
    target = spec.get("hierarchy")
    if target:
        conn = sqlite3.connect(db_path)
        try:
            known = {n for (n,) in conn.execute(
                "SELECT node_id FROM nodes WHERE hierarchy=?", (target,))}
        finally:
            conn.close()
        dropped = sorted({n for (_, _, n) in items if n and n not in known})
        clean = [(u, t, (n if n in known else None)) for (u, t, n) in items]
        _ref, stats, _dangling, _known = import_objectives.load(
            db_path, course, clean, hierarchy=target)
        msg = (f"objectives <- {spec['file']} -> {target}: {stats['objectives_new']} new, "
               f"{stats['pooled']} pooled, {stats['coverage']} coverage")
        if dropped:
            msg += f" ({len(dropped)} unknown id(s) dropped)"
        return msg
    clean = [(u, t, None) for (u, t, _n) in items]  # pool only
    _ref, stats, _dangling, _known = import_objectives.load(db_path, course, clean)
    return (f"objectives <- {spec['file']} (pool): {stats['objectives_new']} new, "
            f"{stats['pooled']} pooled")


def seed(db_path, seed_dir):
    """Create + populate every manifest course that doesn't already exist."""
    manifest = os.path.join(seed_dir, "manifest.toml")
    if not os.path.exists(manifest):
        print(f"seed: no manifest at {manifest}; nothing to load", file=sys.stderr)
        return
    with open(manifest, "rb") as f:
        data = tomllib.load(f)

    for c in data.get("course", []):
        course = c.get("id")
        if not course:
            print("seed: course entry with no id -- skipping", file=sys.stderr)
            continue
        title = c.get("title") or course.upper()
        conn = sqlite3.connect(db_path)
        try:
            if conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
                print(f"seed: course {course!r} already exists -- skipping", file=sys.stderr)
                continue
            conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)", (course, title))
            _ensure_outline(conn, course)
            conn.commit()
        finally:
            conn.close()
        print(f"seed: created course {course!r} ({title!r})", file=sys.stderr)

        for h in c.get("hierarchy", []):
            try:
                slug, n = _load_hierarchy(db_path, course, h, seed_dir)
                print(f"seed:   hierarchy {slug!r} <- {h.get('file')} ({n} nodes)",
                      file=sys.stderr)
            except Exception as e:
                print(f"seed:   WARN hierarchy {h.get('file')!r}: {e}", file=sys.stderr)
        for o in c.get("objectives", []):
            try:
                print(f"seed:   {_load_objectives(db_path, course, o, seed_dir)}",
                      file=sys.stderr)
            except Exception as e:
                print(f"seed:   WARN objectives {o.get('file')!r}: {e}", file=sys.stderr)


def _ensure_schema(db_path):
    conn = sqlite3.connect(db_path)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='courses'").fetchone():
            conn.executescript(open(SCHEMA_PATH).read())
            conn.commit()
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("seed_dir", help="directory containing manifest.toml + input files")
    p.add_argument("database", nargs="?", default="db.db")
    args = p.parse_args()
    _ensure_schema(args.database)
    seed(args.database, args.seed_dir)


if __name__ == "__main__":
    main()
