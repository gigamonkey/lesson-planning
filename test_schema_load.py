#!/usr/bin/env python3
"""Self-contained checks for loading the courses directory into the composite-key schema and
querying it. No test framework: run directly.

    uv run test_schema_load.py

Builds a throwaway db from examples/widgets and asserts the (course, hierarchy)
identity invariants: every hierarchy-scoped row carries its course, slugs are bare
and course-relative, a bundle round-trips under a fresh course id, and the
slug-vs-filename mismatch warning / in-course slug collision both fire.
"""

import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout

import course_bundle
import load_nodes
import plan_io

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGETS = os.path.join(HERE, "examples", "widgets")

# Tables keyed by (course, hierarchy, ...): each must carry the course of its
# hierarchy -- no row may reference a hierarchy of another course.
SCOPED_TABLES = ["nodes", "coverage", "node_attr", "node_duration"]

checks = 0


def check(cond, msg):
    global checks
    if not cond:
        print(f"not ok - {msg}")
        sys.exit(1)
    checks += 1


def fresh_db(course_dir, course):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    plan_io.read_course(path, course_dir)
    return path


def main():
    db = fresh_db(WIDGETS, "widgets")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # 1. Composite identity: hierarchies keyed by (course, bare-slug).
    hs = {(r["course"], r["hierarchy"]): r["editable"]
          for r in conn.execute("SELECT course, hierarchy, editable FROM hierarchies")}
    check(("widgets", "ced") in hs and hs[("widgets", "ced")] == 0,
          "reference (widgets, ced) present and read-only")
    check(("widgets", "plan") in hs and hs[("widgets", "plan")] == 1,
          "outline (widgets, plan) present and editable")
    check(all("-" not in h or h.split("-")[0] != "widgets" for _c, h in hs),
          "slugs are bare (no leading 'widgets-' course prefix)")

    # 2. Every hierarchy-scoped row carries a course matching a real hierarchy.
    valid = set(hs)
    for tbl in SCOPED_TABLES:
        bad = conn.execute(
            f"SELECT count(*) FROM {tbl} t WHERE NOT EXISTS "
            f"(SELECT 1 FROM hierarchies h WHERE h.course=t.course AND h.hierarchy=t.hierarchy)"
        ).fetchone()[0]
        check(bad == 0, f"{tbl}: every row's (course, hierarchy) is a real hierarchy")
    # hierarchy_targets too (both sides are this course's hierarchies).
    bad = conn.execute(
        "SELECT count(*) FROM hierarchy_targets t WHERE NOT EXISTS "
        "(SELECT 1 FROM hierarchies h WHERE h.course=t.course AND h.hierarchy=t.outline)"
    ).fetchone()[0]
    check(bad == 0, "hierarchy_targets: outline resolves within the course")

    # 3. Query: the outline's placements + the reference's coverage are scoped right.
    placed = conn.execute("SELECT count(*) FROM coverage WHERE course='widgets' AND hierarchy='plan'").fetchone()[0]
    ref_cov = conn.execute("SELECT count(*) FROM coverage WHERE course='widgets' AND hierarchy='ced'").fetchone()[0]
    check(placed > 0, "outline has objective placements")
    check(ref_cov > 0, "reference 'ced' has coverage edges")
    # A workspace-style join: objectives placed in the outline are all in the pool.
    orphan = conn.execute(
        "SELECT count(*) FROM coverage cv WHERE cv.course='widgets' AND cv.hierarchy='plan' "
        "AND NOT EXISTS (SELECT 1 FROM course_objectives co "
        "WHERE co.course='widgets' AND co.uuid=cv.uuid)").fetchone()[0]
    check(orphan == 0, "every outline placement is a pooled objective")

    # 4. Bundle export -> import under a NEW course id preserves the data, and bare
    #    slugs don't collide across courses (composite key).
    bundle = course_bundle.export_course(conn, "widgets")
    conn.close()
    fd, db2 = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c2 = sqlite3.connect(db2)
    load_nodes.apply_schema(c2)
    course_bundle.import_course(c2, bundle, course="widgets2")
    c2.commit()
    n1 = sqlite3.connect(db).execute("SELECT count(*) FROM nodes WHERE course='widgets'").fetchone()[0]
    n2 = c2.execute("SELECT count(*) FROM nodes WHERE course='widgets2'").fetchone()[0]
    check(n1 == n2 and n2 > 0, "bundle import preserves node count under a new course id")
    check(c2.execute("SELECT count(*) FROM nodes WHERE course='widgets'").fetchone()[0] == 0,
          "import did not leak rows under the source course id")
    c2.close()

    # 5. Slug mismatch warns; two files with the same pinned slug collide.
    with tempfile.TemporaryDirectory() as d:
        # minimal valid course: a plan + a reference whose filename != pinned slug
        open(os.path.join(d, "plan.md"), "w").write(
            "---\ncourse: t\ntitle: T\nprimary_outline: plan\n---\n\n# Unit: U\n\n## L\n")
        ref = ("---\nslug: ced\nlevels: unit\nkind: ced\ntitle: C\n---\n# Unit 1: X\n")
        open(os.path.join(d, "renamed.md"), "w").write(ref)   # stem 'renamed' != slug 'ced'
        buf = io.StringIO()
        with redirect_stdout(buf):
            p = fresh_db(d, "t")
        check("rename the file" in buf.getvalue(), "filename/slug mismatch warns")
        os.remove(p)

        # add a second file pinning the same slug -> collision error
        open(os.path.join(d, "second.md"), "w").write(ref)
        try:
            fresh_db(d, "t")
            check(False, "duplicate in-course slug should raise")
        except ValueError as e:
            check("resolve to slug 'ced'" in str(e), "duplicate in-course slug raises ValueError")

    for p in (db, db2):
        os.path.exists(p) and os.remove(p)
    print(f"ok - all schema/load checks passed ({checks} assertions)")


if __name__ == "__main__":
    main()
