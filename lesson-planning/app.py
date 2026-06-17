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
import sys
import uuid as uuidlib

from flask import (Flask, abort, flash, redirect, render_template, request,
                   url_for)

# Import the sibling repo-root module (export_planning).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import export_planning  # noqa: E402

DB_PATH = os.environ.get(
    "LESSON_DB", os.path.join(os.path.dirname(__file__), "db.db")
)
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "export")

app = Flask(__name__)
app.secret_key = "lesson-planning-dev"  # local single-user app; not security-sensitive

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


# --------------------------------------------------------------------------
# Objectives + mapping (write views)

def active_objectives(conn, course):
    """Active raw objectives for a course with their coverage node_ids."""
    objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"], "nodes": []}
            for r in conn.execute(
                """SELECT o.uuid, o.text FROM objectives o
                     JOIN course_objectives co
                       ON co.uuid = o.uuid AND co.course = ?
                    WHERE o.status = 'active'""", (course,))}
    for r in conn.execute(
        "SELECT uuid, node_id FROM coverage WHERE course = ?", (course,)):
        if r["uuid"] in objs:
            objs[r["uuid"]]["nodes"].append(r["node_id"])
    return objs


def leaf_choices(conn, course):
    """(node_id, label) for every leaf node, in document order -- for pickers."""
    return [(r["node_id"], (r["text"] or "").split("\n", 1)[0])
            for r in conn.execute(
                "SELECT node_id, text FROM nodes "
                "WHERE course = ? AND is_leaf = 1 ORDER BY ordinal", (course,))]


def node_order(conn, course):
    return {r["node_id"]: r["ordinal"] for r in conn.execute(
        "SELECT node_id, ordinal FROM nodes WHERE course = ?", (course,))}


@app.route("/<course>/objectives")
def objectives(course):
    with db() as conn:
        cs = courses(conn)
        objs = active_objectives(conn, course)
        order = node_order(conn, course)
        leaves = leaf_choices(conn, course)
    # Sort by earliest covered node (CED order), unmapped last, then text.
    def key(o):
        ords = [order.get(n, 10**9) for n in o["nodes"]]
        return (min(ords) if ords else 10**9, o["text"].lower())
    rows = sorted(objs.values(), key=key)
    return render_template(
        "objectives.html", course=course, courses=cs,
        objectives=rows, leaves=leaves, total=len(rows))


def _back(course):
    """Redirect to the posting page, re-anchored to a node if `anchor` was sent.

    Preserves the referrer's query string (e.g. ?filter=gaps) and re-attaches the
    `#anchor` fragment so saving from the long outline keeps your scroll position.
    """
    target = request.referrer or url_for("objectives", course=course)
    anchor = (request.form.get("anchor") or "").strip()
    if anchor:
        target = target.split("#", 1)[0] + "#" + anchor
    return redirect(target)


@app.route("/<course>/objective/new", methods=["POST"])
def objective_new(course):
    text = (request.form.get("text") or "").strip()
    if text:
        u = str(uuidlib.uuid4())
        with db() as conn:
            conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (u, text))
            conn.execute("INSERT INTO course_objectives VALUES (?, ?)", (course, u))
            node = (request.form.get("node_id") or "").strip()
            if node:
                conn.execute("INSERT OR IGNORE INTO coverage VALUES (?, ?, ?)",
                             (course, u, node))
            conn.commit()
        flash(f"Added objective: {text}")
    return _back(course)


@app.route("/<course>/objective/<uuid>/edit", methods=["POST"])
def objective_edit(course, uuid):
    text = (request.form.get("text") or "").strip()
    if text:
        with db() as conn:
            conn.execute("UPDATE objectives SET text = ? WHERE uuid = ?", (text, uuid))
            conn.commit()
        flash("Edited objective.")
    return _back(course)


@app.route("/<course>/objective/<uuid>/coverage/add", methods=["POST"])
def coverage_add(course, uuid):
    node = (request.form.get("node_id") or "").strip()
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM nodes WHERE course = ? AND node_id = ?",
            (course, node)).fetchone()
        if not node or not exists:
            flash(f"No such node: {node!r}")
        else:
            conn.execute("INSERT OR IGNORE INTO coverage VALUES (?, ?, ?)",
                         (course, uuid, node))
            conn.commit()
            flash(f"Mapped to {node}.")
    return _back(course)


@app.route("/<course>/objective/<uuid>/coverage/remove", methods=["POST"])
def coverage_remove(course, uuid):
    node = (request.form.get("node_id") or "").strip()
    with db() as conn:
        conn.execute("DELETE FROM coverage WHERE course = ? AND uuid = ? AND node_id = ?",
                     (course, uuid, node))
        conn.commit()
    flash(f"Unmapped from {node}.")
    return _back(course)


@app.route("/<course>/export", methods=["POST"])
def export(course):
    written = export_planning.export(DB_PATH, EXPORT_DIR)
    flash("Exported snapshot: " + ", ".join(f"{t} ({n})" for t, n in written))
    return redirect(request.referrer or url_for("objectives", course=course))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5001")))
