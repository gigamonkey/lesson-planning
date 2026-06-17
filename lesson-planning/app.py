"""Lesson-planning web app (Phase 1: read-only).

Two views over the lesson-planning database (seeded by load_nodes.py and
import_objectives.py):

- `/<course>`         the official outline as a tree, every leaf badged with its
                      coverage status (gap / objective / planned) and the raw
                      objectives mapped to it. `?filter=gaps` prunes to the gaps.
- `/<course>/report`  the traceability report: summary stats, the full gap list,
                      and every covered leaf with its objective -> lesson chain.

Run:  uv run lesson-planning/app.py        (serves on PORT, default 5001)
The database path defaults to db.db next to this file; override with LESSON_DB.
"""

import html
import os
import re
import sqlite3

from flask import Flask, abort, redirect, render_template, request, url_for

DB_PATH = os.environ.get(
    "LESSON_DB", os.path.join(os.path.dirname(__file__), "db.db")
)

app = Flask(__name__)

# Coverage status -> (label, css class). 'planned' = a scheduled lesson traces
# back to the leaf; 'objective' = a raw objective covers it but nothing is
# scheduled yet; 'gap' = no objective at all.
STATUS = {
    "gap": ("gap", "gap"),
    "objective": ("objective", "objective"),
    "planned": ("planned", "planned"),
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def courses(conn):
    return [r["course"] for r in conn.execute(
        "SELECT DISTINCT course FROM nodes ORDER BY course"
    )]


def load_course(conn, course):
    """Return (nodes, objectives_by_node, planned_leaves) for a course.

    nodes: list of sqlite Rows ordered by document position.
    objectives_by_node: node_id -> list of active raw objectives covering it.
    planned_leaves: set of leaf node_ids that a scheduled lesson traces back to.
    """
    nodes = conn.execute(
        "SELECT * FROM nodes WHERE course = ? ORDER BY ordinal", (course,)
    ).fetchall()
    if not nodes:
        abort(404, f"no nodes loaded for course {course!r}")

    objectives_by_node = {}
    for r in conn.execute(
        """SELECT cv.node_id, o.uuid, o.text
             FROM coverage cv
             JOIN objectives o ON o.uuid = cv.uuid AND o.status = 'active'
            WHERE cv.course = ?
            ORDER BY o.text""",
        (course,),
    ):
        objectives_by_node.setdefault(r["node_id"], []).append(r)

    planned_leaves = {r["node_id"] for r in conn.execute(
        """SELECT DISTINCT cv.node_id
             FROM coverage cv
             JOIN objectives o        ON o.uuid = cv.uuid AND o.status = 'active'
             JOIN objective_rollup rr ON rr.objective_uuid = cv.uuid
             JOIN lesson_objectives lo ON lo.id = rr.lesson_objective_id
            WHERE cv.course = ? AND lo.lesson_id IS NOT NULL""",
        (course,),
    )}
    return nodes, objectives_by_node, planned_leaves


def leaf_status(node, objectives_by_node, planned_leaves):
    if node["node_id"] in planned_leaves:
        return "planned"
    if objectives_by_node.get(node["node_id"]):
        return "objective"
    return "gap"


def build_tree(nodes, objectives_by_node, planned_leaves, gaps_only=False):
    """Build a nested tree of view dicts; optionally prune to gap leaves + ancestors."""
    by_id = {n["node_id"]: n for n in nodes}
    children = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)

    keep = None
    if gaps_only:
        keep = set()
        for n in nodes:
            if n["is_leaf"] and leaf_status(n, objectives_by_node, planned_leaves) == "gap":
                nid = n["node_id"]
                while nid is not None and nid not in keep:
                    keep.add(nid)
                    nid = by_id[nid]["parent_id"] if nid in by_id else None

    def make(n):
        nid = n["node_id"]
        kids = [make(c) for c in children.get(nid, [])
                if keep is None or c["node_id"] in keep]
        return {
            "id": nid,
            "level": n["level"],
            "label": (n["text"] or "").split("\n", 1)[0],
            "is_leaf": bool(n["is_leaf"]),
            "status": leaf_status(n, objectives_by_node, planned_leaves)
                      if n["is_leaf"] else None,
            "objectives": objectives_by_node.get(nid, []),
            "children": kids,
        }

    return [make(n) for n in children.get(None, [])
            if keep is None or n["node_id"] in keep]


def summary(nodes, objectives_by_node, planned_leaves):
    leaves = [n for n in nodes if n["is_leaf"]]
    counts = {"gap": 0, "objective": 0, "planned": 0}
    for n in leaves:
        counts[leaf_status(n, objectives_by_node, planned_leaves)] += 1
    total = len(leaves)
    covered = counts["objective"] + counts["planned"]
    return {
        "leaves": total,
        "gaps": counts["gap"],
        "objective": counts["objective"],
        "planned": counts["planned"],
        "covered": covered,
        "pct_covered": round(100 * covered / total) if total else 0,
        "pct_planned": round(100 * counts["planned"] / total) if total else 0,
    }


INLINE = re.compile(r"`([^`]+)`|\*([^*]+)\*")


@app.template_filter("inline")
def inline(text):
    """Escape HTML, then render markdown `code` and *emphasis* inline."""
    out = INLINE.sub(
        lambda m: f"<code>{html.escape(m.group(1))}</code>" if m.group(1)
        else f"<em>{html.escape(m.group(2))}</em>",
        html.escape(text or ""),
    )
    from markupsafe import Markup
    return Markup(out)


@app.route("/")
def index():
    with db() as conn:
        cs = courses(conn)
    if not cs:
        abort(404, "no courses loaded -- run load_nodes.py first")
    return redirect(url_for("tree", course=cs[0]))


@app.route("/<course>")
def tree(course):
    gaps_only = request.args.get("filter") == "gaps"
    with db() as conn:
        cs = courses(conn)
        nodes, obn, planned = load_course(conn, course)
    return render_template(
        "tree.html",
        course=course, courses=cs, gaps_only=gaps_only,
        stats=summary(nodes, obn, planned),
        tree=build_tree(nodes, obn, planned, gaps_only),
        STATUS=STATUS,
    )


@app.route("/<course>/report")
def report(course):
    with db() as conn:
        cs = courses(conn)
        nodes, obn, planned = load_course(conn, course)
    leaves = [n for n in nodes if n["is_leaf"]]
    gaps = [n for n in leaves
            if leaf_status(n, obn, planned) == "gap"]
    covered = [(n, obn.get(n["node_id"], []),
                leaf_status(n, obn, planned))
               for n in leaves if leaf_status(n, obn, planned) != "gap"]
    return render_template(
        "report.html",
        course=course, courses=cs,
        stats=summary(nodes, obn, planned),
        gaps=gaps, covered=covered, STATUS=STATUS,
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5001")))
