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
from markupsafe import Markup

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


def ensure_schema():
    """Idempotent migrations for an existing working-copy db."""
    try:
        with db() as conn:
            # Generalize nodes/coverage: course -> hierarchy, and a registry.
            ncols = [r[1] for r in conn.execute("PRAGMA table_info(nodes)")]
            if ncols and "course" in ncols:
                conn.execute("ALTER TABLE nodes RENAME COLUMN course TO hierarchy")
                conn.execute("ALTER TABLE coverage RENAME COLUMN course TO hierarchy")
            conn.execute("CREATE TABLE IF NOT EXISTS hierarchies (hierarchy TEXT PRIMARY KEY,"
                         " kind TEXT NOT NULL, title TEXT NOT NULL, source TEXT)")
            if conn.execute("SELECT count(*) FROM hierarchies").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT OR IGNORE INTO hierarchies(hierarchy, kind, title, source)"
                    " VALUES (?, 'reference', ?, NULL)",
                    [(h, h) for (h,) in conn.execute("SELECT DISTINCT hierarchy FROM nodes")])

            cols = [r[1] for r in conn.execute("PRAGMA table_info(course_objectives)")]
            if "position" not in cols:
                conn.execute("ALTER TABLE course_objectives ADD COLUMN position INTEGER")
            if "plan_unit" not in cols:
                conn.execute("ALTER TABLE course_objectives ADD COLUMN plan_unit TEXT")
            if "plan_lesson" not in cols:
                conn.execute("ALTER TABLE course_objectives ADD COLUMN plan_lesson TEXT")

            # Plan tables moved to UUID ids + per-lesson learning objective. The
            # old rollup model is dropped; recreate only if empty (no data loss).
            def empty(t):
                try:
                    return conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0
                except sqlite3.OperationalError:
                    return True
            lcols = [r[1] for r in conn.execute("PRAGMA table_info(lessons)")]
            stale = ("learning_objective" not in lcols) if lcols else True
            if stale and all(empty(t) for t in
                             ("units", "lessons", "lesson_objectives", "objective_rollup")):
                for t in ("objective_rollup", "lesson_objectives", "lessons", "units"):
                    conn.execute(f"DROP TABLE IF EXISTS {t}")
                conn.execute(
                    "CREATE TABLE units (uuid TEXT PRIMARY KEY, course TEXT NOT NULL,"
                    " title TEXT NOT NULL, position INTEGER NOT NULL)")
                conn.execute(
                    "CREATE TABLE lessons (uuid TEXT PRIMARY KEY, course TEXT NOT NULL,"
                    " unit_id TEXT, title TEXT NOT NULL DEFAULT '',"
                    " learning_objective TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL)")

            # Drop the unused objectives.merged_into column. It carries a self-FK,
            # which a plain DROP COLUMN can't remove, so rebuild the table (FKs are
            # off, so dropping the referenced table is fine).
            if "merged_into" in [r[1] for r in conn.execute("PRAGMA table_info(objectives)")]:
                conn.execute("DROP TABLE IF EXISTS objectives_new")
                conn.execute("CREATE TABLE objectives_new (uuid TEXT PRIMARY KEY,"
                             " text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')")
                conn.execute("INSERT INTO objectives_new(uuid, text, status)"
                             " SELECT uuid, text, status FROM objectives")
                conn.execute("DROP TABLE objectives")
                conn.execute("ALTER TABLE objectives_new RENAME TO objectives")
            conn.commit()
    except sqlite3.OperationalError:
        pass  # tables not created yet (unseeded db)


def courses(conn):
    return [r["course"] for r in conn.execute(
        "SELECT DISTINCT course FROM course_objectives ORDER BY course"
    )]


def load_course(conn, course):
    """Return (nodes, objectives_by_node, planned_leaves) for a course.

    nodes: list of sqlite Rows ordered by document position.
    objectives_by_node: node_id -> list of active raw objectives covering it.
    planned_leaves: set of leaf node_ids that a scheduled lesson traces back to.
    """
    nodes = conn.execute(
        "SELECT * FROM nodes WHERE hierarchy = ? ORDER BY ordinal", (course,)
    ).fetchall()
    if not nodes:
        abort(404, f"no nodes loaded for course {course!r}")

    objectives_by_node = {}
    for r in conn.execute(
        """SELECT cv.node_id, o.uuid, o.text
             FROM coverage cv
             JOIN objectives o ON o.uuid = cv.uuid AND o.status = 'active'
            WHERE cv.hierarchy = ?
            ORDER BY o.text""",
        (course,),
    ):
        objectives_by_node.setdefault(r["node_id"], []).append(r)

    # A leaf is "planned" once a raw objective covering it is placed in a lesson.
    planned_leaves = {r["node_id"] for r in conn.execute(
        """SELECT DISTINCT cv.node_id
             FROM coverage cv
             JOIN objectives o         ON o.uuid = cv.uuid AND o.status = 'active'
             JOIN course_objectives co ON co.uuid = cv.uuid AND co.course = cv.hierarchy
            WHERE cv.hierarchy = ? AND co.plan_lesson IS NOT NULL""",
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
            "text": n["text"] or "",
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
BULLET = re.compile(r"^\s*-\s+(.*)$")


def _inline(text):
    """Escape HTML, then render markdown `code` and *emphasis* inline -> str."""
    return INLINE.sub(
        lambda m: f"<code>{html.escape(m.group(1))}</code>" if m.group(1)
        else f"<em>{html.escape(m.group(2))}</em>",
        html.escape(text or ""),
    )


@app.template_filter("inline")
def inline(text):
    return Markup(_inline(text))


@app.template_filter("blocks")
def blocks(text):
    """Render multi-line node text: paragraphs and `- ` bullet lists, with inline
    code/em -- so a leaf's full text (a sentence plus a bulleted list) shows."""
    out, para, items = [], [], []

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_list():
        if items:
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            items.clear()

    for line in (text or "").split("\n"):
        if not line.strip():
            flush_para(); flush_list(); continue
        m = BULLET.match(line)
        if m:
            flush_para(); items.append(_inline(m.group(1)))
        else:
            flush_list(); para.append(_inline(line))
    flush_para(); flush_list()
    return Markup("".join(out))


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
        "SELECT uuid, node_id FROM coverage WHERE hierarchy = ?", (course,)):
        if r["uuid"] in objs:
            objs[r["uuid"]]["nodes"].append(r["node_id"])
    return objs


def leaf_choices(conn, course):
    """(node_id, label) for every leaf node, in document order -- for pickers."""
    return [(r["node_id"], (r["text"] or "").split("\n", 1)[0])
            for r in conn.execute(
                "SELECT node_id, text FROM nodes "
                "WHERE hierarchy = ? AND is_leaf = 1 ORDER BY ordinal", (course,))]


def node_order(conn, course):
    return {r["node_id"]: r["ordinal"] for r in conn.execute(
        "SELECT node_id, ordinal FROM nodes WHERE hierarchy = ?", (course,))}


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


def leafbox_response(course, node_id):
    """Render the leaf's objectives box partial (the htmx swap target)."""
    with db() as conn:
        objs = conn.execute(
            """SELECT o.uuid, o.text FROM coverage cv
                 JOIN objectives o ON o.uuid = cv.uuid AND o.status = 'active'
                WHERE cv.hierarchy = ? AND cv.node_id = ? ORDER BY o.text""",
            (course, node_id)).fetchall()
    return render_template("_leafbox.html", course=course,
                           node_id=node_id, objectives=objs)


@app.route("/<course>/leafbox/<node_id>")
def leafbox(course, node_id):
    """The objectives box partial for one leaf (used to refresh after a drag)."""
    return leafbox_response(course, node_id)


@app.route("/<course>/outline-stats")
def outline_stats(course):
    """The outline's coverage stats bar partial (refreshed after add/recategorize)."""
    with db() as conn:
        nodes, obn, planned = load_course(conn, course)
    return render_template("_outline_stats.html", stats=summary(nodes, obn, planned))


@app.route("/<course>/objective/<uuid>/recategorize", methods=["POST"])
def recategorize(course, uuid):
    """Move an objective's coverage from one leaf to another (drag/drop)."""
    from_node = (request.form.get("from_node") or "").strip()
    to_node = (request.form.get("to_node") or "").strip()
    with db() as conn:
        valid = conn.execute("SELECT 1 FROM nodes WHERE hierarchy=? AND node_id=?",
                             (course, to_node)).fetchone()
        if to_node and valid and to_node != from_node:
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                         "VALUES (?, ?, ?)", (course, uuid, to_node))
            conn.execute("DELETE FROM coverage WHERE hierarchy=? AND uuid=? AND node_id=?",
                         (course, uuid, from_node))
            conn.commit()
    return ("", 204)


@app.route("/<course>/objective/new", methods=["POST"])
def objective_new(course):
    text = (request.form.get("text") or "").strip()
    node = (request.form.get("node_id") or "").strip()
    if text:
        u = str(uuidlib.uuid4())
        with db() as conn:
            conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (u, text))
            conn.execute("INSERT INTO course_objectives(course, uuid) VALUES (?, ?)", (course, u))
            if node:
                conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                             "VALUES (?, ?, ?)", (course, u, node))
            conn.commit()
    # htmx (outline): swap just this leaf's box; otherwise PRG back to the page.
    if request.headers.get("HX-Request"):
        return leafbox_response(course, node)
    if text:
        flash(f"Added objective: {text}")
    return _back(course)


@app.route("/<course>/objective/<uuid>/edit", methods=["POST"])
def objective_edit(course, uuid):
    text = (request.form.get("text") or "").strip()
    if text:
        with db() as conn:
            conn.execute("UPDATE objectives SET text = ? WHERE uuid = ?", (text, uuid))
            conn.commit()
    # An edit doesn't change structure/coverage, so no re-render -- just persist.
    if request.headers.get("HX-Request"):
        return ("", 204)
    if text:
        flash("Edited objective.")
    return _back(course)


@app.route("/<course>/objective/<uuid>/coverage/add", methods=["POST"])
def coverage_add(course, uuid):
    node = (request.form.get("node_id") or "").strip()
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM nodes WHERE hierarchy = ? AND node_id = ?",
            (course, node)).fetchone()
        if not node or not exists:
            flash(f"No such node: {node!r}")
        else:
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                         "VALUES (?, ?, ?)", (course, uuid, node))
            conn.commit()
            flash(f"Mapped to {node}.")
    return _back(course)


@app.route("/<course>/objective/<uuid>/coverage/remove", methods=["POST"])
def coverage_remove(course, uuid):
    node = (request.form.get("node_id") or "").strip()
    with db() as conn:
        conn.execute("DELETE FROM coverage WHERE hierarchy = ? AND uuid = ? AND node_id = ?",
                     (course, uuid, node))
        conn.commit()
    flash(f"Unmapped from {node}.")
    return _back(course)


@app.route("/<course>/export", methods=["POST"])
def export(course):
    written, pruned = export_planning.export(DB_PATH, EXPORT_DIR)
    msg = "Exported snapshot: " + ", ".join(f"{t} ({n})" for t, n in written)
    if pruned:
        msg += " · removed " + ", ".join(pruned)
    flash(msg)
    return redirect(request.referrer or url_for("objectives", course=course))


# --------------------------------------------------------------------------
# Lesson builder: synthesize raw -> lesson objectives, then schedule into lessons

def worklist_counts(conn, course):
    """Plan-progress counts: CED gaps, unplaced/rough raws, planned-leaf coverage."""
    def scalar(sql):
        return conn.execute(sql, (course,)).fetchone()[0]

    leaves = scalar("SELECT count(*) FROM nodes WHERE hierarchy=? AND is_leaf=1")
    gaps = scalar("""
        SELECT count(*) FROM nodes n WHERE n.hierarchy=? AND n.is_leaf=1
          AND NOT EXISTS (
            SELECT 1 FROM coverage cv JOIN objectives o
                   ON o.uuid=cv.uuid AND o.status='active'
             WHERE cv.hierarchy=n.hierarchy AND cv.node_id=n.node_id)""")
    planned = scalar("""
        SELECT count(*) FROM nodes n WHERE n.hierarchy=? AND n.is_leaf=1
          AND EXISTS (
            SELECT 1 FROM coverage cv
              JOIN objectives o         ON o.uuid=cv.uuid AND o.status='active'
              JOIN course_objectives co ON co.uuid=cv.uuid AND co.course=cv.hierarchy
             WHERE cv.hierarchy=n.hierarchy AND cv.node_id=n.node_id
               AND co.plan_lesson IS NOT NULL)""")

    def raw_count(where):
        return conn.execute(
            f"""SELECT count(*) FROM objectives o
                  JOIN course_objectives co ON co.uuid=o.uuid AND co.course=?
                 WHERE o.status='active' AND {where}""", (course,)).fetchone()[0]
    unplaced = raw_count("co.plan_unit IS NULL AND co.plan_lesson IS NULL")
    rough = raw_count("co.plan_unit IS NOT NULL AND co.plan_lesson IS NULL")
    return {"leaves": leaves, "gaps": gaps, "planned": planned,
            "unplaced": unplaced, "rough": rough,
            "planned_pct": round(100 * planned / leaves) if leaves else 0}


def _id_list(field="ids"):
    """Read an id list sent either as repeated fields or one comma-joined field."""
    vals = request.form.getlist(field)
    if len(vals) == 1 and "," in vals[0]:
        vals = vals[0].split(",")
    return [v for v in (s.strip() for s in vals) if v]


# --------------------------------------------------------------------------
# The Plan page: Units -> Lessons, with raw objectives placed into them.

def plan_data(conn, course):
    """(counts, units[+lessons+rough raws], unassigned lessons, pool raws)."""
    counts = worklist_counts(conn, course)
    order = node_order(conn, course)
    objs = active_objectives(conn, course)
    for r in conn.execute(
        "SELECT uuid, position, plan_unit, plan_lesson FROM course_objectives "
        "WHERE course=?", (course,)):
        if r["uuid"] in objs:
            o = objs[r["uuid"]]
            o["position"], o["plan_unit"], o["plan_lesson"] = (
                r["position"], r["plan_unit"], r["plan_lesson"])

    def rawkey(o):
        p = o.get("position")
        ords = [order.get(n, 10**9) for n in o["nodes"]]
        return ((0, p, "") if p is not None
                else (1, min(ords) if ords else 10**9, o["text"].lower()))

    by_lesson, rough_by_unit, pool = {}, {}, []
    for o in sorted(objs.values(), key=rawkey):
        if o.get("plan_lesson"):
            by_lesson.setdefault(o["plan_lesson"], []).append(o)
        elif o.get("plan_unit"):
            rough_by_unit.setdefault(o["plan_unit"], []).append(o)
        else:
            pool.append(o)

    lessons = [dict(r) for r in conn.execute(
        "SELECT uuid, unit_id, title, learning_objective FROM lessons "
        "WHERE course=? ORDER BY position, uuid", (course,))]
    lessons_by_unit = {}
    for L in lessons:
        L["raws"] = by_lesson.get(L["uuid"], [])
        lessons_by_unit.setdefault(L["unit_id"], []).append(L)

    units = [dict(r) for r in conn.execute(
        "SELECT uuid, title FROM units WHERE course=? ORDER BY position, uuid", (course,))]
    for u in units:
        u["lessons"] = lessons_by_unit.get(u["uuid"], [])
        u["rough"] = rough_by_unit.get(u["uuid"], [])
    unassigned = lessons_by_unit.get(None, [])
    return counts, units, unassigned, pool


@app.route("/<course>/plan")
def plan(course):
    with db() as conn:
        cs = courses(conn)
        counts, units, unassigned, pool = plan_data(conn, course)
    return render_template("plan.html", course=course, courses=cs, counts=counts,
                           units=units, unassigned=unassigned, pool=pool)


@app.route("/<course>/plan-stats")
def plan_stats(course):
    with db() as conn:
        counts, units, unassigned, pool = plan_data(conn, course)
    return render_template("_plan_stats.html", course=course, counts=counts,
                           pool_count=len(pool))


@app.route("/<course>/plan/place", methods=["POST"])
def plan_place(course):
    """Place a raw objective via drag.

    Form: `uuid`, `to` container ("pool" | "unit-<uuid>" | "lesson-<uuid>"), and
    `ids` (the pool's new order, when to == pool). Deeper level wins; the pool
    clears both placements.
    """
    uuid = request.form.get("uuid")
    to = (request.form.get("to") or "").strip()
    plan_unit = to[5:] if to.startswith("unit-") else None
    plan_lesson = to[7:] if to.startswith("lesson-") else None
    with db() as conn:
        conn.execute("UPDATE course_objectives SET plan_unit=?, plan_lesson=? "
                     "WHERE course=? AND uuid=?", (plan_unit, plan_lesson, course, uuid))
        if to == "pool":
            for i, u in enumerate(_id_list("ids")):
                conn.execute("UPDATE course_objectives SET position=? "
                             "WHERE course=? AND uuid=?", (i, course, u))
        conn.commit()
    return ("", 204)


# --- Units ---

@app.route("/<course>/unit/new", methods=["POST"])
def unit_new(course):
    title = (request.form.get("title") or "").strip()
    if title:
        with db() as conn:
            nxt = conn.execute("SELECT COALESCE(MAX(position), -1)+1 FROM units "
                               "WHERE course=?", (course,)).fetchone()[0]
            conn.execute("INSERT INTO units(uuid, course, title, position) VALUES (?, ?, ?, ?)",
                         (str(uuidlib.uuid4()), course, title, nxt))
            conn.commit()
        flash(f"Added unit: {title}")
    return _back(course)


@app.route("/<course>/unit/<unit_id>/rename", methods=["POST"])
def unit_rename(course, unit_id):
    title = (request.form.get("title") or "").strip()
    if title:
        with db() as conn:
            conn.execute("UPDATE units SET title=? WHERE uuid=? AND course=?",
                         (title, unit_id, course))
            conn.commit()
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/unit/<unit_id>/delete", methods=["POST"])
def unit_delete(course, unit_id):
    with db() as conn:
        # Unassign its lessons; return its rough raws to the pool; drop the unit.
        conn.execute("UPDATE lessons SET unit_id=NULL WHERE unit_id=?", (unit_id,))
        conn.execute("UPDATE course_objectives SET plan_unit=NULL "
                     "WHERE course=? AND plan_unit=?", (course, unit_id))
        conn.execute("DELETE FROM units WHERE uuid=? AND course=?", (unit_id, course))
        conn.commit()
    flash("Deleted unit; lessons moved to Unassigned, rough raws back in the pool.")
    return _back(course)


@app.route("/<course>/unit/<unit_id>/move", methods=["POST"])
def unit_move(course, unit_id):
    direction = request.form.get("dir")
    with db() as conn:
        ids = [r["uuid"] for r in conn.execute(
            "SELECT uuid FROM units WHERE course=? ORDER BY position, uuid", (course,))]
        if unit_id in ids:
            i = ids.index(unit_id)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for pos, uid in enumerate(ids):
                    conn.execute("UPDATE units SET position=? WHERE uuid=?", (pos, uid))
                conn.commit()
    return _back(course)


# --- Lessons ---

@app.route("/<course>/lesson/new", methods=["POST"])
def lesson_new(course):
    title = (request.form.get("title") or "").strip()
    unit = (request.form.get("unit") or "").strip() or None
    with db() as conn:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(position), -1)+1 FROM lessons "
            "WHERE course=? AND unit_id IS ?", (course, unit)).fetchone()[0]
        conn.execute(
            "INSERT INTO lessons(uuid, course, unit_id, title, learning_objective, position) "
            "VALUES (?, ?, ?, ?, '', ?)", (str(uuidlib.uuid4()), course, unit, title, nxt))
        conn.commit()
    flash("Added lesson.")
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/edit", methods=["POST"])
def lesson_edit(course, lesson_id):
    """Edit a lesson's title and/or learning objective (only sent fields change)."""
    with db() as conn:
        if "title" in request.form:
            conn.execute("UPDATE lessons SET title=? WHERE uuid=? AND course=?",
                         ((request.form.get("title") or "").strip(), lesson_id, course))
        if "learning_objective" in request.form:
            conn.execute(
                "UPDATE lessons SET learning_objective=? WHERE uuid=? AND course=?",
                ((request.form.get("learning_objective") or "").strip(), lesson_id, course))
        conn.commit()
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/delete", methods=["POST"])
def lesson_delete(course, lesson_id):
    with db() as conn:
        # Return its raws to the pool, then drop the lesson.
        conn.execute("UPDATE course_objectives SET plan_unit=NULL, plan_lesson=NULL "
                     "WHERE course=? AND plan_lesson=?", (course, lesson_id))
        conn.execute("DELETE FROM lessons WHERE uuid=? AND course=?", (lesson_id, course))
        conn.commit()
    flash("Deleted lesson; its raws returned to the pool.")
    return _back(course)


@app.route("/<course>/lesson/arrange", methods=["POST"])
def lesson_arrange(course):
    """Drag lessons between units / reorder. Form: `unit` (uuid or ""/"none") + `ids`."""
    unit = (request.form.get("unit") or "").strip()
    unit_id = None if unit in ("", "none") else unit
    with db() as conn:
        for pos, lid in enumerate(_id_list("ids")):
            conn.execute("UPDATE lessons SET unit_id=?, position=? WHERE uuid=? AND course=?",
                         (unit_id, pos, lid, course))
        conn.commit()
    return ("", 204)


ensure_schema()


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5001")))
