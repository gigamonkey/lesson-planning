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

import csv
import html
import io
import os
import re
import sqlite3
import sys
import uuid as uuidlib

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, url_for)
from markupsafe import Markup

# Import sibling repo-root modules (export_planning, import_objectives).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import export_planning  # noqa: E402
import import_objectives  # noqa: E402

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

            conn.execute("CREATE TABLE IF NOT EXISTS node_attr (hierarchy TEXT NOT NULL,"
                         " node_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,"
                         " PRIMARY KEY (hierarchy, node_id, name))")
            conn.execute("CREATE TABLE IF NOT EXISTS hierarchy_targets (outline TEXT NOT NULL,"
                         " reference TEXT NOT NULL, PRIMARY KEY (outline, reference))")

            # Stage 1: the lesson plan becomes an 'outline' hierarchy. Convert the
            # old units/lessons tables + plan_unit/plan_lesson placement into
            # nodes + coverage + node_attr, then drop them.
            have = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "units" in have and "lessons" in have:
                plan_courses = [c for (c,) in conn.execute(
                    "SELECT course FROM units UNION SELECT course FROM lessons "
                    "UNION SELECT course FROM course_objectives")]
                for course in plan_courses:
                    O = course + "-plan"
                    conn.execute(
                        "INSERT OR IGNORE INTO hierarchies(hierarchy, kind, title, source)"
                        " VALUES (?, 'outline', ?, NULL)", (O, course.upper() + " Lesson Plan"))
                    conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                                 " VALUES (?, ?)", (O, course))
                    for u in conn.execute(
                        "SELECT uuid, title, position FROM units WHERE course=?",
                        (course,)).fetchall():
                        conn.execute(
                            "INSERT OR IGNORE INTO nodes(hierarchy, node_id, parent_id, level,"
                            " is_leaf, ordinal, text) VALUES (?, ?, NULL, 'unit', 0, ?, ?)",
                            (O, u["uuid"], u["position"], u["title"]))
                    for L in conn.execute(
                        "SELECT uuid, unit_id, title, learning_objective, position"
                        " FROM lessons WHERE course=?", (course,)).fetchall():
                        conn.execute(
                            "INSERT OR IGNORE INTO nodes(hierarchy, node_id, parent_id, level,"
                            " is_leaf, ordinal, text) VALUES (?, ?, ?, 'lesson', 1, ?, ?)",
                            (O, L["uuid"], L["unit_id"], L["position"], L["title"] or ""))
                        if (L["learning_objective"] or "").strip():
                            conn.execute(
                                "INSERT OR IGNORE INTO node_attr(hierarchy, node_id, name, value)"
                                " VALUES (?, ?, 'learning_objective', ?)",
                                (O, L["uuid"], L["learning_objective"]))
                    for r in conn.execute(
                        "SELECT uuid, plan_unit, plan_lesson FROM course_objectives"
                        " WHERE course=?", (course,)).fetchall():
                        node = r["plan_lesson"] or r["plan_unit"]
                        if node:
                            conn.execute(
                                "INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id)"
                                " VALUES (?, ?, ?)", (O, r["uuid"], node))
                conn.execute("DROP TABLE IF EXISTS lessons")
                conn.execute("DROP TABLE IF EXISTS units")
                # Rebuild course_objectives without the plan_unit/plan_lesson columns.
                conn.execute("CREATE TABLE course_objectives_new (course TEXT NOT NULL,"
                             " uuid TEXT NOT NULL REFERENCES objectives(uuid),"
                             " position INTEGER, PRIMARY KEY (course, uuid))")
                conn.execute("INSERT INTO course_objectives_new(course, uuid, position)"
                             " SELECT course, uuid, position FROM course_objectives")
                conn.execute("DROP TABLE course_objectives")
                conn.execute("ALTER TABLE course_objectives_new RENAME TO course_objectives")

            # Stage 2: courses become first-class and hierarchies carry an explicit
            # course + kind (the TYPE) + editable flag; the old kind ('reference'/
            # 'outline') splits into kind/editable, and a reference's slug is renamed
            # off the course id (e.g. 'csa' -> 'csa-ced') so the slug is a pure handle.
            hcols = [r[1] for r in conn.execute("PRAGMA table_info(hierarchies)")]
            if hcols and "course" not in hcols:
                conn.execute("CREATE TABLE IF NOT EXISTS courses "
                             "(course TEXT PRIMARY KEY, title TEXT NOT NULL)")
                targets = dict(conn.execute(
                    "SELECT outline, reference FROM hierarchy_targets").fetchall())
                COURSE_TITLES = {"csa": "AP Computer Science A",
                                 "csp": "AP Computer Science Principles",
                                 "ib": "IB Computer Science"}
                REF_KIND = {"ib": ("ib-syllabus", "ib-syllabus")}  # course -> (kind, new slug)
                new_rows, renames, course_set = [], {}, set()
                for slug, kind, title, source in conn.execute(
                        "SELECT hierarchy, kind, title, source FROM hierarchies").fetchall():
                    if kind == "outline":
                        course = targets.get(slug, slug)  # ref slug == course (pre-rename)
                        new_rows.append((slug, course, "lesson-plan", 1, title, source))
                    else:  # reference: slug currently == the course id
                        course = slug
                        rkind, newslug = REF_KIND.get(course, ("ced", course + "-ced"))
                        renames[slug] = newslug
                        rtitle = (title if title and title != slug
                                  else f"{course.upper()} {rkind.replace('-', ' ').upper()}")
                        new_rows.append((newslug, course, rkind, 0, rtitle, source))
                    course_set.add(course)
                for old_slug, new_slug in renames.items():
                    for tbl, col in [("nodes", "hierarchy"), ("coverage", "hierarchy"),
                                     ("node_attr", "hierarchy"), ("hierarchy_targets", "reference")]:
                        conn.execute(f"UPDATE {tbl} SET {col}=? WHERE {col}=?",
                                     (new_slug, old_slug))
                conn.executemany("INSERT OR IGNORE INTO courses(course, title) VALUES (?, ?)",
                                 [(c, COURSE_TITLES.get(c, c.upper())) for c in sorted(course_set)])
                conn.execute("DROP TABLE hierarchies")
                conn.execute("CREATE TABLE hierarchies (hierarchy TEXT PRIMARY KEY,"
                             " course TEXT NOT NULL REFERENCES courses(course), kind TEXT NOT NULL,"
                             " editable INTEGER NOT NULL, title TEXT NOT NULL, source TEXT)")
                conn.executemany(
                    "INSERT INTO hierarchies(hierarchy, course, kind, editable, title, source)"
                    " VALUES (?, ?, ?, ?, ?, ?)", new_rows)

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

            # Enforce unique objective text: merge any duplicate-text objectives
            # onto one survivor (repointing coverage + pool membership), then add
            # the unique index. Idempotent -- a no-op once text is unique.
            if "objectives" in {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                for (text,) in conn.execute(
                        "SELECT text FROM objectives GROUP BY text HAVING count(*)>1").fetchall():
                    uuids = [u for (u,) in conn.execute(
                        "SELECT uuid FROM objectives WHERE text=? ORDER BY uuid", (text,))]
                    keep, drop = uuids[0], uuids[1:]
                    for d in drop:
                        conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id)"
                                     " SELECT hierarchy, ?, node_id FROM coverage WHERE uuid=?",
                                     (keep, d))
                        conn.execute("DELETE FROM coverage WHERE uuid=?", (d,))
                        conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid, position)"
                                     " SELECT course, ?, position FROM course_objectives WHERE uuid=?",
                                     (keep, d))
                        conn.execute("DELETE FROM course_objectives WHERE uuid=?", (d,))
                        conn.execute("DELETE FROM objectives WHERE uuid=?", (d,))
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS objectives_text_unique"
                             " ON objectives(text)")
            conn.commit()
    except sqlite3.OperationalError:
        pass  # tables not created yet (unseeded db)


def courses(conn):
    return [r["course"] for r in conn.execute(
        "SELECT course FROM courses ORDER BY course")]


# A course is backed by hierarchies (the courses->hierarchies link). Its REFERENCE
# (a read-only CED/syllabus) and its OUTLINE (the authored lesson plan) are resolved
# by the explicit course/kind/editable columns -- never by parsing the slug. Both
# reference "coverage" and lesson "placement" are coverage edges into a hierarchy.

def reference_hierarchy(conn, course):
    """The slug of the course's reference hierarchy (the CED/syllabus), or None."""
    row = conn.execute(
        "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0 "
        "ORDER BY (kind='ced') DESC, hierarchy LIMIT 1", (course,)).fetchone()
    return row[0] if row else None


def outline_hierarchy(conn, course):
    """The slug of the course's outline hierarchy (its plan), or None if none yet."""
    row = conn.execute(
        "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 "
        "ORDER BY (kind='lesson-plan') DESC, hierarchy LIMIT 1", (course,)).fetchone()
    return row[0] if row else None


def ensure_outline(conn, course):
    """The course's outline hierarchy slug, creating + registering it if needed."""
    O = outline_hierarchy(conn, course)
    if O:
        return O
    O = course + "-plan"  # a readable handle; the columns below carry the meaning
    conn.execute("INSERT OR IGNORE INTO courses(course, title) VALUES (?, ?)",
                 (course, course.upper()))
    conn.execute("INSERT OR IGNORE INTO hierarchies(hierarchy, course, kind, editable, title,"
                 " source) VALUES (?, ?, 'lesson-plan', 1, ?, NULL)",
                 (O, course, course.upper() + " Lesson Plan"))
    # Measure the plan against each of the course's references.
    conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                 " SELECT ?, hierarchy FROM hierarchies WHERE course=? AND editable=0",
                 (O, course))
    return O


@app.context_processor
def inject_nav():
    """Sidebar data for every page: courses -> their hierarchies (references then
    outlines), plus the course/hierarchy the current request is showing."""
    nav, active = [], None
    va = request.view_args or {}
    nav_course = va.get("course")
    try:
        with db() as conn:
            cs = conn.execute("SELECT course, title FROM courses ORDER BY course").fetchall()
            by_course = {}
            for h in conn.execute(
                "SELECT hierarchy, course, kind, editable, title FROM hierarchies "
                "ORDER BY course, editable, (kind='ced') DESC, hierarchy"):
                by_course.setdefault(h["course"], []).append(h)
            nav = [{"course": c["course"], "title": c["title"],
                    "hierarchies": by_course.get(c["course"], [])} for c in cs]
            if "hierarchy" in va:
                active = va["hierarchy"]
            elif nav_course and request.endpoint == "plan":
                active = outline_hierarchy(conn, nav_course)
            elif nav_course and request.endpoint == "tree":
                active = reference_hierarchy(conn, nav_course)
    except sqlite3.OperationalError:
        pass
    return {"course_nav": nav, "active_hierarchy": active, "nav_course": nav_course}


def load_course(conn, course, R=None):
    """Return (nodes, objectives_by_node, planned_leaves) for a reference.

    R is the reference hierarchy to render (default: the course's primary one).

    nodes: list of sqlite Rows ordered by document position.
    objectives_by_node: node_id -> list of active raw objectives covering it.
    planned_leaves: set of leaf node_ids that a scheduled lesson traces back to.
    """
    R = R or reference_hierarchy(conn, course)
    nodes = conn.execute(
        "SELECT * FROM nodes WHERE hierarchy = ? ORDER BY ordinal", (R,)
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
        (R,),
    ):
        objectives_by_node.setdefault(r["node_id"], []).append(r)

    # A reference leaf is "planned" once a raw objective covering it is also placed
    # at a leaf (a lesson) of the course's outline hierarchy.
    O = outline_hierarchy(conn, course)
    planned_leaves = set()
    if O:
        planned_leaves = {r["node_id"] for r in conn.execute(
            """SELECT DISTINCT cr.node_id
                 FROM coverage cr
                 JOIN objectives o  ON o.uuid = cr.uuid AND o.status = 'active'
                 JOIN coverage co   ON co.uuid = cr.uuid AND co.hierarchy = ?
                 JOIN nodes onode   ON onode.hierarchy = ? AND onode.node_id = co.node_id
                                   AND onode.is_leaf = 1
                WHERE cr.hierarchy = ?""",
            (O, O, R),
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


def synthetic_ids(nodes):
    """Positional display ids for a hierarchy whose node_ids are uuids (an outline):
    pre-order '1', '1.1', '1.2', '2.1', ... reflecting the current structure.

    Returns {node_id: (display, seq)} -- display is the dotted id; seq is a global
    pre-order index for document-order sorting. `nodes` rows need node_id/parent_id/
    ordinal. Identity stays the uuid; these are computed fresh each render.
    """
    children = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: (n["ordinal"], n["node_id"]))
    out, seq = {}, [0]

    def walk(parent, prefix):
        for i, n in enumerate(children.get(parent, []), 1):
            disp = f"{prefix}.{i}" if prefix else str(i)
            out[n["node_id"]] = (disp, seq[0])
            seq[0] += 1
            walk(n["node_id"], disp)

    walk(None, "")
    return out


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


@app.route("/favicon.ico")
def favicon():
    """Serve the favicon at the root too, for browsers' automatic /favicon.ico probe."""
    return app.send_static_file("favicon.ico")


@app.route("/help")
def help_page():
    """A static explainer: the data model, the lifecycle, and how to add material."""
    with db() as conn:
        rows = conn.execute(
            "SELECT c.course, c.title, "
            "  (SELECT count(*) FROM hierarchies h WHERE h.course=c.course AND h.editable=0) refs, "
            "  (SELECT count(*) FROM hierarchies h WHERE h.course=c.course AND h.editable=1) plans "
            "FROM courses c ORDER BY c.course").fetchall()
    cs = [r["course"] for r in rows]
    return render_template("help.html", courses=cs, course=(cs[0] if cs else None),
                           course_rows=rows)


def workspace_data(conn, course, H):
    """Node tree of hierarchy H with the raw objectives mapped onto each node, plus
    the unplaced pool. Single placement per hierarchy: an objective sits under one
    node of H or in the pool. Each objective also carries `tags` -- its node ids in
    OTHER reference hierarchies -- as cross-reference hints (outline placements are
    omitted: their node ids are uuids, not meaningful labels)."""
    refs = {r[0] for r in conn.execute("SELECT hierarchy FROM hierarchies WHERE editable=0")}
    objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"], "position": r["position"],
                        "node": None, "tags": []}
            for r in conn.execute(
                "SELECT o.uuid, o.text, co.position FROM objectives o "
                "JOIN course_objectives co ON co.uuid=o.uuid AND co.course=? "
                "WHERE o.status='active'", (course,))}
    for r in conn.execute(
        "SELECT cv.uuid, cv.hierarchy, cv.node_id FROM coverage cv "
        "JOIN course_objectives co ON co.uuid=cv.uuid AND co.course=?", (course,)):
        o = objs.get(r["uuid"])
        if not o:
            continue
        if r["hierarchy"] == H:
            o["node"] = r["node_id"]
        elif r["hierarchy"] in refs:
            o["tags"].append(r["node_id"])
    for o in objs.values():
        o["tags"] = sorted(set(o["tags"]))

    by_node = {}
    for o in sorted(objs.values(), key=lambda o: o["text"].lower()):
        if o["node"]:
            by_node.setdefault(o["node"], []).append(o)
    pool = sorted((o for o in objs.values() if not o["node"]),
                  key=lambda o: (0, o["position"]) if o["position"] is not None
                  else (1, o["text"].lower()))

    nodes = conn.execute("SELECT * FROM nodes WHERE hierarchy=? ORDER BY ordinal", (H,)).fetchall()
    return nodes, by_node, pool


def workspace_stats(nodes, by_node, pool):
    leaves = [n for n in nodes if n["is_leaf"]]
    covered = sum(1 for n in leaves if by_node.get(n["node_id"]))
    return {"leaves": len(leaves), "covered": covered, "gaps": len(leaves) - covered,
            "pool": len(pool), "pct": round(100 * covered / len(leaves)) if leaves else 0}


@app.route("/<course>")
def tree(course):
    with db() as conn:
        R = reference_hierarchy(conn, course)
    if not R:
        abort(404, f"no reference hierarchy for course {course!r}")
    return redirect(url_for("hierarchy_view", course=course, hierarchy=R))


@app.route("/<course>/h/<hierarchy>")
def hierarchy_view(course, hierarchy):
    """The unified workspace for any hierarchy: its node tree with a droppable zone
    per node + the raw-objective pool. Editable hierarchies also edit structure."""
    with db() as conn:
        h = conn.execute("SELECT * FROM hierarchies WHERE hierarchy=? AND course=?",
                         (hierarchy, course)).fetchone()
        if not h:
            abort(404, f"no hierarchy {hierarchy!r} for course {course!r}")
        nodes, by_node, pool = workspace_data(conn, course, hierarchy)
        los = {r["node_id"]: r["value"] for r in conn.execute(
            "SELECT node_id, value FROM node_attr "
            "WHERE hierarchy=? AND name='learning_objective'", (hierarchy,))} \
            if h["editable"] else {}
    return render_template(
        "workspace.html", course=course, ref=hierarchy, hierarchy_title=h["title"],
        kind=h["kind"], editable=bool(h["editable"]), los=los, pool=pool,
        tree=build_tree(nodes, by_node, set()),
        stats=workspace_stats(nodes, by_node, pool))


@app.route("/<course>/h/<hierarchy>/stats")
def workspace_stats_partial(course, hierarchy):
    with db() as conn:
        nodes, by_node, pool = workspace_data(conn, course, hierarchy)
    return render_template("_wstats.html", course=course, ref=hierarchy,
                           stats=workspace_stats(nodes, by_node, pool))


@app.route("/<course>/h/<hierarchy>/place", methods=["POST"])
def place(course, hierarchy):
    """Drag a raw objective: `to` is "node-<id>" (map/recategorize) or "pool"
    (unmap, + reorder via `ids`). Single placement per hierarchy."""
    uuid = request.form.get("uuid")
    to = (request.form.get("to") or "").strip()
    node = to[5:] if to.startswith("node-") else None
    with db() as conn:
        conn.execute("DELETE FROM coverage WHERE hierarchy=? AND uuid=?", (hierarchy, uuid))
        if node:
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                         "VALUES (?, ?, ?)", (hierarchy, uuid, node))
        if to == "pool":
            for i, u in enumerate(_id_list("ids")):
                conn.execute("UPDATE course_objectives SET position=? "
                             "WHERE course=? AND uuid=?", (i, course, u))
        conn.commit()
    return ("", 204)


@app.route("/<course>/h/<hierarchy>/upload", methods=["POST"])
def hierarchy_upload(course, hierarchy):
    """Import an uploaded file (import_objectives) with coverage into THIS hierarchy.
    Node ids not in the hierarchy are dropped (objective kept in the pool) and
    reported, so a mis-classified id doesn't strand an objective on a phantom node."""
    back = redirect(url_for("hierarchy_view", course=course, hierarchy=hierarchy))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return back
    try:
        items, mode = import_objectives.parse_text(f.read().decode("utf-8", "replace"))
    except ValueError as e:
        flash(f"Upload failed: {e}")
        return back
    with db() as conn:
        known = {r[0] for r in conn.execute(
            "SELECT node_id FROM nodes WHERE hierarchy=?", (hierarchy,))}
    unknown = sorted({n for _, _, n in items if n and n not in known})
    clean = [(u, t, (n if n in known else None)) for u, t, n in items]
    _, stats, _, _ = import_objectives.load(DB_PATH, course, clean, hierarchy=hierarchy)
    msg = (f"Imported {f.filename!r} ({mode}) into {hierarchy}: {stats['objectives_new']} "
           f"new, {stats['pooled']} added to the pool, {stats['coverage']} coverage edges")
    if unknown:
        msg += (f" · {len(unknown)} id(s) not in this hierarchy (left in the pool): "
                f"{', '.join(unknown[:6])}{'…' if len(unknown) > 6 else ''}")
    flash(msg)
    return back


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
        "SELECT uuid, node_id FROM coverage WHERE hierarchy = ?",
        (reference_hierarchy(conn, course),)):
        if r["uuid"] in objs:
            objs[r["uuid"]]["nodes"].append(r["node_id"])
    return objs


def leaf_choices(conn, course):
    """(node_id, label) for every leaf node, in document order -- for pickers."""
    return [(r["node_id"], (r["text"] or "").split("\n", 1)[0])
            for r in conn.execute(
                "SELECT node_id, text FROM nodes "
                "WHERE hierarchy = ? AND is_leaf = 1 ORDER BY ordinal",
                (reference_hierarchy(conn, course),))]


def node_order(conn, course):
    return {r["node_id"]: r["ordinal"] for r in conn.execute(
        "SELECT node_id, ordinal FROM nodes WHERE hierarchy = ?",
        (reference_hierarchy(conn, course),))}


@app.route("/<course>/objectives")
def objectives(course):
    """A compact table of the course's objectives: a text column plus one column
    per reference hierarchy, each cell holding the (kind-colored) node ids the
    objective covers there. Headers sort -- text lexically, a hierarchy column by
    document order (node ordinal; lexical-by-id does NOT match it)."""
    BIG = 10 ** 9
    with db() as conn:
        cols = [dict(r) for r in conn.execute(
            "SELECT hierarchy, kind, editable, title FROM hierarchies WHERE course=? "
            "ORDER BY editable, (kind='ced') DESC, hierarchy", (course,))]
        slugs = [h["hierarchy"] for h in cols]
        # (hierarchy, node_id) -> (display_id, sort_ord, tooltip). References use the
        # verbatim id + document ordinal; editable outlines use synthetic ids.
        node_meta = {}
        for h in cols:
            ns = conn.execute(
                "SELECT node_id, parent_id, ordinal, text FROM nodes WHERE hierarchy=?",
                (h["hierarchy"],)).fetchall()
            firstline = lambda t: re.sub(r"[`*]", "", (t or "").split("\n", 1)[0])
            if h["editable"]:
                los = {r["node_id"]: r["value"] for r in conn.execute(
                    "SELECT node_id, value FROM node_attr "
                    "WHERE hierarchy=? AND name='learning_objective'", (h["hierarchy"],))}
                sids = synthetic_ids(ns)
                for n in ns:
                    disp, seq = sids[n["node_id"]]
                    tip = (n["text"] or "").strip() or los.get(n["node_id"], "")
                    node_meta[(h["hierarchy"], n["node_id"])] = (disp, seq, firstline(tip))
            else:
                for n in ns:
                    node_meta[(h["hierarchy"], n["node_id"])] = (
                        n["node_id"], n["ordinal"], firstline(n["text"]))
        objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"],
                            "cells": {s: {"tags": [], "ord": BIG} for s in slugs}}
                for r in conn.execute(
                    "SELECT o.uuid, o.text FROM objectives o JOIN course_objectives co "
                    "ON co.uuid=o.uuid AND co.course=? WHERE o.status='active'", (course,))}
        for r in conn.execute(
            "SELECT cv.uuid, cv.hierarchy, cv.node_id FROM coverage cv "
            "JOIN course_objectives co ON co.uuid=cv.uuid AND co.course=?", (course,)):
            o = objs.get(r["uuid"])
            if not o or r["hierarchy"] not in slugs:
                continue
            disp, ordn, title = node_meta.get((r["hierarchy"], r["node_id"]),
                                               (r["node_id"], BIG, ""))
            cell = o["cells"][r["hierarchy"]]
            cell["tags"].append({"id": disp, "title": title, "ord": ordn})
            cell["ord"] = min(cell["ord"], ordn)
        for o in objs.values():
            o["sort"] = re.sub(r"[`*]", "", o["text"]).lower()  # visible-text sort key
            for cell in o["cells"].values():
                cell["tags"].sort(key=lambda t: t["ord"])
    rows = sorted(objs.values(), key=lambda o: o["sort"])
    return render_template("objectives.html", course=course, objectives=rows,
                           hierarchies=cols, total=len(rows))


@app.route("/<course>/objectives.tsv")
def objectives_tsv(course):
    """Download the course's objectives as a uuid/text TSV."""
    with db() as conn:
        rows = conn.execute(
            "SELECT o.uuid, o.text FROM objectives o JOIN course_objectives co "
            "ON co.uuid=o.uuid AND co.course=? WHERE o.status='active' ORDER BY o.text",
            (course,)).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(["uuid", "text"])
    writer.writerows([(r["uuid"], r["text"]) for r in rows])
    return Response(
        buf.getvalue(), mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f'attachment; filename="{course}-objectives.tsv"'})


@app.route("/<course>/objectives/upload", methods=["POST"])
def objectives_upload(course):
    """Upload a file of objectives and import it via import_objectives (plain text
    or a uuid/objective/node_id TSV); coverage lands in the course's reference."""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(url_for("objectives", course=course))
    try:
        items, mode = import_objectives.parse_text(f.read().decode("utf-8", "replace"))
    except ValueError as e:
        flash(f"Upload failed: {e}")
        return redirect(url_for("objectives", course=course))
    ref, stats, dangling, known = import_objectives.load(DB_PATH, course, items)
    msg = (f"Imported {f.filename!r} ({mode}): read {stats['read']}, "
           f"{stats['objectives_new']} new objectives, {stats['pooled']} added to the "
           f"pool, {stats['coverage']} coverage edges into {ref}")
    if dangling:
        msg += f" · {len(dangling)} unknown node id(s): {', '.join(dangling[:6])}"
    flash(msg)
    return redirect(url_for("objectives", course=course))


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


def active_ref(conn, course):
    """The reference hierarchy this request targets (a `ref` arg/field), else the
    course's primary reference -- so the tree view and its AJAX endpoints all act
    on whichever hierarchy is being shown (e.g. csa-ced vs csa-book)."""
    return (request.values.get("ref") or "").strip() or reference_hierarchy(conn, course)


def leafbox_response(course, node_id, R):
    """Render the leaf's objectives box partial (the htmx swap target)."""
    with db() as conn:
        objs = conn.execute(
            """SELECT o.uuid, o.text FROM coverage cv
                 JOIN objectives o ON o.uuid = cv.uuid AND o.status = 'active'
                WHERE cv.hierarchy = ? AND cv.node_id = ? ORDER BY o.text""",
            (R, node_id)).fetchall()
    return render_template("_leafbox.html", course=course, ref=R,
                           node_id=node_id, objectives=objs)


@app.route("/<course>/leafbox/<node_id>")
def leafbox(course, node_id):
    """The objectives box partial for one leaf (used to refresh after a drag)."""
    with db() as conn:
        R = active_ref(conn, course)
    return leafbox_response(course, node_id, R)


@app.route("/<course>/outline-stats")
def outline_stats(course):
    """The outline's coverage stats bar partial (refreshed after add/recategorize)."""
    with db() as conn:
        nodes, obn, planned = load_course(conn, course, active_ref(conn, course))
    return render_template("_outline_stats.html", stats=summary(nodes, obn, planned))


@app.route("/<course>/objective/<uuid>/recategorize", methods=["POST"])
def recategorize(course, uuid):
    """Move an objective's coverage from one leaf to another (drag/drop)."""
    from_node = (request.form.get("from_node") or "").strip()
    to_node = (request.form.get("to_node") or "").strip()
    with db() as conn:
        R = active_ref(conn, course)
        valid = conn.execute("SELECT 1 FROM nodes WHERE hierarchy=? AND node_id=?",
                             (R, to_node)).fetchone()
        if to_node and valid and to_node != from_node:
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                         "VALUES (?, ?, ?)", (R, uuid, to_node))
            conn.execute("DELETE FROM coverage WHERE hierarchy=? AND uuid=? AND node_id=?",
                         (R, uuid, from_node))
            conn.commit()
    return ("", 204)


@app.route("/<course>/objective/new", methods=["POST"])
def objective_new(course):
    text = (request.form.get("text") or "").strip()
    node = (request.form.get("node_id") or "").strip()
    u = None
    if text:
        with db() as conn:
            R = active_ref(conn, course)
            # Intern by text: reuse the existing objective, or create a new one.
            row = conn.execute("SELECT uuid FROM objectives WHERE text=?", (text,)).fetchone()
            u = row[0] if row else str(uuidlib.uuid4())
            if not row:
                conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (u, text))
            conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid) VALUES (?, ?)",
                         (course, u))
            if node:
                conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                             "VALUES (?, ?, ?)", (R, u, node))
            conn.commit()
    # Workspace (htmx): return just the new raw item to drop into the target zone.
    if request.form.get("as") == "item":
        if not text:
            return ("", 204)
        return render_template("_rawitem.html", o={"uuid": u, "text": text, "tags": []})
    if text:
        flash(f"Added objective: {text}")
    return _back(course)


@app.route("/<course>/objective/<uuid>/edit", methods=["POST"])
def objective_edit(course, uuid):
    text = (request.form.get("text") or "").strip()
    if text:
        with db() as conn:
            # Text is the natural key: refuse an edit that would duplicate another
            # objective (the user should map both to a node, not retype the text).
            clash = conn.execute("SELECT 1 FROM objectives WHERE text=? AND uuid<>?",
                                 (text, uuid)).fetchone()
            if clash:
                if request.headers.get("HX-Request"):
                    return ("an objective with that text already exists", 409)
                flash("Not saved: an objective with that text already exists.")
                return _back(course)
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
        R = reference_hierarchy(conn, course)
        exists = conn.execute(
            "SELECT 1 FROM nodes WHERE hierarchy = ? AND node_id = ?",
            (R, node)).fetchone()
        if not node or not exists:
            flash(f"No such node: {node!r}")
        else:
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                         "VALUES (?, ?, ?)", (R, uuid, node))
            conn.commit()
            flash(f"Mapped to {node}.")
    return _back(course)


@app.route("/<course>/objective/<uuid>/coverage/remove", methods=["POST"])
def coverage_remove(course, uuid):
    node = (request.form.get("node_id") or "").strip()
    with db() as conn:
        conn.execute("DELETE FROM coverage WHERE hierarchy = ? AND uuid = ? AND node_id = ?",
                     (reference_hierarchy(conn, course), uuid, node))
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
    """Plan-progress counts: reference gaps, unplaced/rough raws, planned coverage.

    Computed for the course's outline O against its reference R (=course): a
    reference leaf is planned when an active objective covers it AND is placed at
    an outline leaf (lesson); a raw is rough when placed at a non-leaf (unit) and
    unplaced when it has no outline edge.
    """
    R = reference_hierarchy(conn, course)
    O = outline_hierarchy(conn, course)

    def scalar(sql, params):
        return conn.execute(sql, params).fetchone()[0]

    leaves = scalar("SELECT count(*) FROM nodes WHERE hierarchy=? AND is_leaf=1", (R,))
    gaps = scalar("""
        SELECT count(*) FROM nodes n WHERE n.hierarchy=? AND n.is_leaf=1
          AND NOT EXISTS (
            SELECT 1 FROM coverage cv JOIN objectives o
                   ON o.uuid=cv.uuid AND o.status='active'
             WHERE cv.hierarchy=n.hierarchy AND cv.node_id=n.node_id)""", (R,))

    def raw_total(extra):
        return scalar(f"""SELECT count(*) FROM objectives o
              JOIN course_objectives co ON co.uuid=o.uuid AND co.course=?
             WHERE o.status='active'{extra}""", (course,))

    if O:
        planned = scalar("""
            SELECT count(*) FROM nodes n WHERE n.hierarchy=? AND n.is_leaf=1
              AND EXISTS (
                SELECT 1 FROM coverage cr
                  JOIN objectives o ON o.uuid=cr.uuid AND o.status='active'
                  JOIN coverage co  ON co.uuid=cr.uuid AND co.hierarchy=?
                  JOIN nodes onode  ON onode.hierarchy=? AND onode.node_id=co.node_id
                                   AND onode.is_leaf=1
                 WHERE cr.hierarchy=n.hierarchy AND cr.node_id=n.node_id)""",
            (R, O, O))
        unplaced = scalar("""SELECT count(*) FROM objectives o
              JOIN course_objectives co ON co.uuid=o.uuid AND co.course=?
             WHERE o.status='active' AND NOT EXISTS (
                SELECT 1 FROM coverage cv WHERE cv.hierarchy=? AND cv.uuid=o.uuid)""",
            (course, O))
        rough = scalar("""SELECT count(*) FROM objectives o
              JOIN course_objectives co ON co.uuid=o.uuid AND co.course=?
             WHERE o.status='active' AND EXISTS (
                SELECT 1 FROM coverage cv
                  JOIN nodes nn ON nn.hierarchy=cv.hierarchy AND nn.node_id=cv.node_id
                 WHERE cv.hierarchy=? AND cv.uuid=o.uuid AND nn.is_leaf=0)""",
            (course, O))
    else:
        planned, rough = 0, 0
        unplaced = raw_total("")
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

def outline_structure(conn, O):
    """(units, lessons, node_level, learning_objectives) for an outline hierarchy.

    units/lessons are view dicts in sibling order; node_level maps every outline
    node_id to its level ('unit'|'lesson'); learning_objectives maps lesson id ->
    its learning-objective text.
    """
    units, lessons, node_level = [], [], {}
    if O:
        for n in conn.execute(
            "SELECT node_id, parent_id, level, text FROM nodes "
            "WHERE hierarchy=? ORDER BY ordinal, node_id", (O,)):
            node_level[n["node_id"]] = n["level"]
            if n["level"] == "lesson":
                lessons.append({"uuid": n["node_id"], "unit_id": n["parent_id"],
                                "title": n["text"]})
            elif n["level"] == "unit":
                units.append({"uuid": n["node_id"], "title": n["text"]})
    los = {r["node_id"]: r["value"] for r in conn.execute(
        "SELECT node_id, value FROM node_attr "
        "WHERE hierarchy=? AND name='learning_objective'", (O,))} if O else {}
    return units, lessons, node_level, los


def plan_data(conn, course):
    """(counts, units[+lessons+rough raws], unassigned lessons, pool raws)."""
    counts = worklist_counts(conn, course)
    order = node_order(conn, course)
    objs = active_objectives(conn, course)
    for r in conn.execute(
        "SELECT uuid, position FROM course_objectives WHERE course=?", (course,)):
        if r["uuid"] in objs:
            objs[r["uuid"]]["position"] = r["position"]

    O = outline_hierarchy(conn, course)
    units, lessons, node_level, los = outline_structure(conn, O)
    placed = {r["uuid"]: r["node_id"] for r in conn.execute(
        "SELECT uuid, node_id FROM coverage WHERE hierarchy=?", (O,))} if O else {}

    def rawkey(o):
        p = o.get("position")
        ords = [order.get(n, 10**9) for n in o["nodes"]]
        return ((0, p, "") if p is not None
                else (1, min(ords) if ords else 10**9, o["text"].lower()))

    by_lesson, rough_by_unit, pool = {}, {}, []
    for o in sorted(objs.values(), key=rawkey):
        node = placed.get(o["uuid"])
        level = node_level.get(node)
        if level == "lesson":
            by_lesson.setdefault(node, []).append(o)
        elif level == "unit":
            rough_by_unit.setdefault(node, []).append(o)
        else:
            pool.append(o)

    lessons_by_unit = {}
    for L in lessons:
        L["learning_objective"] = los.get(L["uuid"], "")
        L["raws"] = by_lesson.get(L["uuid"], [])
        lessons_by_unit.setdefault(L["unit_id"], []).append(L)
    for u in units:
        u["lessons"] = lessons_by_unit.get(u["uuid"], [])
        u["rough"] = rough_by_unit.get(u["uuid"], [])
    unassigned = lessons_by_unit.get(None, [])
    return counts, units, unassigned, pool


@app.route("/<course>/plan")
def plan(course):
    """Back-compat: the plan is now the outline hierarchy's workspace."""
    with db() as conn:
        O = outline_hierarchy(conn, course) or ensure_outline(conn, course)
        conn.commit()
    return redirect(url_for("hierarchy_view", course=course, hierarchy=O))


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
    node = to[5:] if to.startswith("unit-") else to[7:] if to.startswith("lesson-") else None
    with db() as conn:
        O = outline_hierarchy(conn, course)
        if O:
            # Single placement per outline: clear this raw's edges, then re-place.
            conn.execute("DELETE FROM coverage WHERE hierarchy=? AND uuid=?", (O, uuid))
            if node:
                conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id) "
                             "VALUES (?, ?, ?)", (O, uuid, node))
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
            O = ensure_outline(conn, course)
            nxt = conn.execute("SELECT COALESCE(MAX(ordinal), -1)+1 FROM nodes "
                               "WHERE hierarchy=? AND level='unit'", (O,)).fetchone()[0]
            conn.execute("INSERT INTO nodes(hierarchy, node_id, parent_id, level, is_leaf,"
                         " ordinal, text) VALUES (?, ?, NULL, 'unit', 0, ?, ?)",
                         (O, str(uuidlib.uuid4()), nxt, title))
            conn.commit()
        flash(f"Added unit: {title}")
    return _back(course)


@app.route("/<course>/unit/<unit_id>/rename", methods=["POST"])
def unit_rename(course, unit_id):
    title = (request.form.get("title") or "").strip()
    if title:
        with db() as conn:
            O = outline_hierarchy(conn, course)
            conn.execute("UPDATE nodes SET text=? WHERE hierarchy=? AND node_id=? "
                         "AND level='unit'", (title, O, unit_id))
            conn.commit()
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/unit/<unit_id>/delete", methods=["POST"])
def unit_delete(course, unit_id):
    with db() as conn:
        O = outline_hierarchy(conn, course)
        # Unassign its lessons; return its rough raws to the pool; drop the unit.
        conn.execute("UPDATE nodes SET parent_id=NULL WHERE hierarchy=? AND parent_id=?",
                     (O, unit_id))
        conn.execute("DELETE FROM coverage WHERE hierarchy=? AND node_id=?", (O, unit_id))
        conn.execute("DELETE FROM node_attr WHERE hierarchy=? AND node_id=?", (O, unit_id))
        conn.execute("DELETE FROM nodes WHERE hierarchy=? AND node_id=?", (O, unit_id))
        conn.commit()
    flash("Deleted unit; lessons moved to Unassigned, rough raws back in the pool.")
    return _back(course)


@app.route("/<course>/unit/<unit_id>/move", methods=["POST"])
def unit_move(course, unit_id):
    direction = request.form.get("dir")
    with db() as conn:
        O = outline_hierarchy(conn, course)
        ids = [r["node_id"] for r in conn.execute(
            "SELECT node_id FROM nodes WHERE hierarchy=? AND level='unit' "
            "ORDER BY ordinal, node_id", (O,))]
        if unit_id in ids:
            i = ids.index(unit_id)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for pos, uid in enumerate(ids):
                    conn.execute("UPDATE nodes SET ordinal=? WHERE hierarchy=? AND node_id=?",
                                 (pos, O, uid))
                conn.commit()
    return _back(course)


# --- Lessons ---

@app.route("/<course>/lesson/new", methods=["POST"])
def lesson_new(course):
    title = (request.form.get("title") or "").strip()
    unit = (request.form.get("unit") or "").strip() or None
    with db() as conn:
        O = ensure_outline(conn, course)
        nxt = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1)+1 FROM nodes "
            "WHERE hierarchy=? AND level='lesson' AND parent_id IS ?", (O, unit)).fetchone()[0]
        conn.execute(
            "INSERT INTO nodes(hierarchy, node_id, parent_id, level, is_leaf, ordinal, text) "
            "VALUES (?, ?, ?, 'lesson', 1, ?, ?)", (O, str(uuidlib.uuid4()), unit, nxt, title))
        conn.commit()
    flash("Added lesson.")
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/edit", methods=["POST"])
def lesson_edit(course, lesson_id):
    """Edit a lesson's title and/or learning objective (only sent fields change)."""
    with db() as conn:
        O = outline_hierarchy(conn, course)
        if "title" in request.form:
            conn.execute("UPDATE nodes SET text=? WHERE hierarchy=? AND node_id=? "
                         "AND level='lesson'",
                         ((request.form.get("title") or "").strip(), O, lesson_id))
        if "learning_objective" in request.form:
            lo = (request.form.get("learning_objective") or "").strip()
            if lo:
                conn.execute(
                    "INSERT INTO node_attr(hierarchy, node_id, name, value) "
                    "VALUES (?, ?, 'learning_objective', ?) "
                    "ON CONFLICT(hierarchy, node_id, name) DO UPDATE SET value=excluded.value",
                    (O, lesson_id, lo))
            else:
                conn.execute("DELETE FROM node_attr WHERE hierarchy=? AND node_id=? "
                             "AND name='learning_objective'", (O, lesson_id))
        conn.commit()
    if request.headers.get("HX-Request"):
        return ("", 204)
    return _back(course)


@app.route("/<course>/lesson/<lesson_id>/delete", methods=["POST"])
def lesson_delete(course, lesson_id):
    with db() as conn:
        O = outline_hierarchy(conn, course)
        # Return its raws to the pool, then drop the lesson.
        conn.execute("DELETE FROM coverage WHERE hierarchy=? AND node_id=?", (O, lesson_id))
        conn.execute("DELETE FROM node_attr WHERE hierarchy=? AND node_id=?", (O, lesson_id))
        conn.execute("DELETE FROM nodes WHERE hierarchy=? AND node_id=?", (O, lesson_id))
        conn.commit()
    flash("Deleted lesson; its raws returned to the pool.")
    return _back(course)


@app.route("/<course>/lesson/arrange", methods=["POST"])
def lesson_arrange(course):
    """Drag lessons between units / reorder. Form: `unit` (uuid or ""/"none") + `ids`."""
    unit = (request.form.get("unit") or "").strip()
    unit_id = None if unit in ("", "none") else unit
    with db() as conn:
        O = outline_hierarchy(conn, course)
        for pos, lid in enumerate(_id_list("ids")):
            conn.execute("UPDATE nodes SET parent_id=?, ordinal=? WHERE hierarchy=? "
                         "AND node_id=? AND level='lesson'", (unit_id, pos, O, lid))
        conn.commit()
    return ("", 204)


ensure_schema()


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5001")))
