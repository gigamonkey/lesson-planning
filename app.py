"""Lesson-planning web app over the database seeded by load_nodes.py and
import_objectives.py. A left sidebar lists courses and, under each, its
Objectives view and its hierarchies. Main views:

- `/<course>/objectives`        a sortable table of the course's raw objectives,
                                a column per hierarchy showing the ids it covers.
- `/<course>/h/<hierarchy>`     the workspace for any hierarchy: its node tree
                                with a droppable zone per node + the raw-objective
                                pool. Editable outlines also edit their structure.

Run:  uv run app.py        (binds HOST:PORT, default 127.0.0.1:5001)
The database path defaults to db.db next to this file; override with LESSON_DB.
"""

import csv
import html
import io
import json
import os
import re
import sqlite3
import sys
import uuid as uuidlib

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, url_for)
from markupsafe import Markup

# Import sibling repo-root modules (the lesson-planning scripts). The app wires
# their library functions to routes -- it never reimplements their logic -- so the
# CLI and the app stay in lockstep.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)
import course_bundle  # noqa: E402
import export_planning  # noqa: E402
import import_objectives  # noqa: E402
import load_nodes  # noqa: E402
import rebuild_db  # noqa: E402
import render_outline  # noqa: E402

DB_PATH = os.environ.get(
    "LESSON_DB", os.path.join(os.path.dirname(__file__), "db.db")
)
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "export")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

app = Flask(__name__)
app.secret_key = "lesson-planning-dev"  # local single-user app; not security-sensitive

# kind_label / hierarchy_title (the page/sidebar titles) live in load_nodes.py so
# load_nodes stores the same clean titles. page_title is an alias for clarity.
page_title = load_nodes.hierarchy_title


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    """Apply the canonical schema to a fresh/empty db, then run idempotent
    migrations on an existing working-copy db.

    A first run (no db.db) thus boots into a valid, empty database -- ready to be
    populated from the app (Data page: load a reference, or restore a snapshot) --
    instead of dead-ending at "no courses loaded". schema.sql is the same canonical
    file rebuild_db applies, so this adds no second source of truth.
    """
    try:
        with db() as conn:
            if "courses" not in {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                conn.executescript(open(SCHEMA_PATH).read())
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

            # The lesson plan is elevated to the course outline: rename the kind
            # and title (idempotent -- a no-op once renamed).
            conn.execute("UPDATE hierarchies SET kind='course-outline', title='Course outline'"
                         " WHERE kind='lesson-plan'")
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
    """The slug of the course's outline hierarchy (its course outline), or None."""
    row = conn.execute(
        "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1 "
        "ORDER BY (kind='course-outline') DESC, hierarchy LIMIT 1", (course,)).fetchone()
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
                 " source) VALUES (?, ?, 'course-outline', 1, 'Course outline', NULL)",
                 (O, course))
    # Measure the plan against each of the course's references.
    conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                 " SELECT ?, hierarchy FROM hierarchies WHERE course=? AND editable=0",
                 (O, course))
    return O


@app.context_processor
def inject_nav():
    """Sidebar data for every page: courses, each with its course outline pulled
    out (elevated as the first item) and its other hierarchies, plus the
    course/hierarchy the current request is showing."""
    nav = []
    va = request.view_args or {}
    nav_course = va.get("course")
    active = va.get("hierarchy")  # set only on the workspace (hierarchy_view)
    try:
        with db() as conn:
            cs = conn.execute("SELECT course, title FROM courses ORDER BY course").fetchall()
            by_course = {}
            for h in conn.execute(
                "SELECT hierarchy, course, kind, editable, title FROM hierarchies "
                "ORDER BY course, editable, (kind='ced') DESC, hierarchy"):
                by_course.setdefault(h["course"], []).append(
                    {"hierarchy": h["hierarchy"], "kind": h["kind"],
                     "editable": h["editable"], "label": h["title"]})
            for c in cs:
                hs = by_course.get(c["course"], [])
                outline = next((h["hierarchy"] for h in hs
                                if h["editable"] and h["kind"] == "course-outline"), None)
                nav.append({"course": c["course"], "title": c["title"], "outline": outline,
                            "hierarchies": [h for h in hs if h["hierarchy"] != outline]})
    except sqlite3.OperationalError:
        pass
    return {"course_nav": nav, "active_hierarchy": active, "nav_course": nav_course}


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


INLINE = re.compile(r"`([^`]+)`|\*([^*]+)\*")


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


@app.route("/")
def index():
    with db() as conn:
        cs = courses(conn)
    if not cs:
        return redirect(url_for("data"))  # empty db: land on the setup/Data page
    return redirect(url_for("plan", course=cs[0]))   # land on the course outline


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


# --------------------------------------------------------------------------
# Data: bootstrap & populate from files / version control. These wire the
# load_nodes / import_planning / rebuild_db library functions to the UI so the
# whole lifecycle -- start empty, load real data, snapshot -- happens in the app.

@app.route("/data")
def data():
    """Settings page: the global version-control operations (restore + export).
    Creating courses and loading hierarchies now live in the sidebar (+) and the
    per-course setup page. Also the empty-db landing page (see `index`)."""
    with db() as conn:
        cs = conn.execute("SELECT course, title FROM courses ORDER BY course").fetchall()
        n_obj = conn.execute("SELECT count(*) FROM objectives").fetchone()[0]
    return render_template("data.html", courses=cs, n_obj=n_obj,
                           export_dir=os.path.relpath(EXPORT_DIR, REPO_ROOT),
                           page_title="Settings")


@app.route("/data/restore", methods=["POST"])
def data_restore():
    """Restore everything from version control: load any reference nodes from the
    configured hierarchy markdown files (none by default in this generic tool --
    upload yours via the form above first), then the planning tables from the
    export dir (rebuild_db's populate step -- WITHOUT the destructive db-file
    delete)."""
    specs = [dict(s, path=os.path.join(REPO_ROOT, s["path"]))
             for s in rebuild_db.DEFAULT_HIERARCHIES]
    node_loads, table_loads = rebuild_db.populate(DB_PATH, EXPORT_DIR, specs)
    refs = ", ".join(f"{slug} ({n})" for _, slug, _, n in node_loads if slug) or "none"
    tables = ", ".join(f"{t} ({n})" for t, n in table_loads) or "none"
    missing = [os.path.relpath(p, REPO_ROOT) for p, slug, _, _ in node_loads if slug is None]
    msg = f"Restored from version control · references: {refs} · planning: {tables}"
    if missing:
        msg += f" · skipped missing file(s): {', '.join(missing)}"
    flash(msg)
    return redirect(url_for("data"))


# --------------------------------------------------------------------------
# Course-first setup: create a course, then manage its hierarchies on a
# per-course Setup page (the sidebar drives this; see templates/base.html).

COURSE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


@app.route("/course/new", methods=["POST"])
def course_new():
    """Create an empty course (id + title) and give it an outline straight away,
    so it's immediately complete and shows in the sidebar. Course id is the
    /<course> URL slug: lowercase letters, digits, hyphens."""
    course = (request.form.get("course") or "").strip().lower()
    title = (request.form.get("title") or "").strip()
    back = request.referrer or url_for("index")
    if not course:
        flash("Course id is required.")
        return redirect(back)
    if not COURSE_ID_RE.match(course):
        flash(f"Invalid course id {course!r}: use lowercase letters, digits, and hyphens.")
        return redirect(back)
    with db() as conn:
        if conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            flash(f"Course {course!r} already exists.")
            return redirect(url_for("setup", course=course))
        conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)",
                     (course, title or course.upper()))
        ensure_outline(conn, course)
    flash(f"Created course {course!r}. Add a hierarchy to get started.")
    return redirect(url_for("setup", course=course))


@app.route("/<course>/setup")
def setup(course):
    """Per-course admin surface: its reference hierarchies (add / delete) plus
    course-level actions. The course outline is managed on the plan page, not
    here."""
    with db() as conn:
        crow = conn.execute("SELECT title FROM courses WHERE course=?", (course,)).fetchone()
        if not crow:
            abort(404)
        refs = conn.execute(
            "SELECT hierarchy, kind, title, source FROM hierarchies "
            "WHERE course=? AND editable=0 ORDER BY (kind='ced') DESC, hierarchy",
            (course,)).fetchall()
        counts = {r["hierarchy"]: r["n"] for r in conn.execute(
            "SELECT hierarchy, count(*) n FROM nodes GROUP BY hierarchy")}
    refs = [dict(r, nodes=counts.get(r["hierarchy"], 0)) for r in refs]
    return render_template("setup.html", course=course, refs=refs,
                           course_title=crow["title"], page_title=f"{course.upper()} setup")


@app.route("/<course>/hierarchy/load", methods=["POST"])
def hierarchy_load_course(course):
    """Load an uploaded node-list JSON as a reference hierarchy of THIS course.
    The course is fixed by context; flavor comes from the document; kind defaults
    from the flavor and the slug defaults to <course>-<kind> (NOT the flavor's own
    slug). Optional form fields override kind / slug / title."""
    with db() as conn:
        crow = conn.execute("SELECT title FROM courses WHERE course=?", (course,)).fetchone()
        if not crow:
            abort(404)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(url_for("setup", course=course))
    try:
        doc = load_nodes.load_doc(json.loads(f.read().decode("utf-8", "replace")))
        flavor = doc["flavor"]
    except Exception as e:  # bad JSON, unsupported version, or missing flavor
        flash(f"Could not load {f.filename!r}: {e}")
        return redirect(url_for("setup", course=course))
    over = lambda k: (request.form.get(k) or "").strip() or None
    kind = over("kind") or load_nodes.meta_for(flavor)["kind"]
    slug = over("hierarchy") or f"{course}-{kind}"
    title = over("title")  # hierarchy display title; None -> derived from course+kind
    m = load_nodes.meta_for(flavor, course=course, kind=kind, slug=slug)
    rows = load_nodes.build_rows(m["slug"], doc["nodes"])
    # Re-loading replaces this hierarchy's nodes; warn (don't drop) about coverage
    # edges into ids the new version no longer has (a renamed/removed id surfaces).
    new_ids = {r[1] for r in rows}
    with db() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT DISTINCT node_id FROM coverage WHERE hierarchy=?", (m["slug"],))}
    orphaned = sorted(existing - new_ids)
    load_nodes.load(DB_PATH, m["slug"], m["course"], m["kind"], crow["title"],
                    rows, source=f.filename, title=title)
    # Measure the course outline against this new reference (the eager outline was
    # created before any reference existed, so link it here).
    with db() as conn:
        O = outline_hierarchy(conn, course)
        if O:
            conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                         " VALUES (?, ?)", (O, m["slug"]))
    leaves = sum(r[4] for r in rows)
    msg = (f"Loaded {f.filename!r} ({flavor}) as {m['slug']}: "
           f"{len(rows)} nodes, {leaves} leaves.")
    if orphaned:
        msg += (f" · warning: {len(orphaned)} existing coverage edge(s) now point to "
                f"node ids not in this version: {', '.join(orphaned[:6])}"
                f"{'…' if len(orphaned) > 6 else ''}")
    flash(msg)
    return redirect(url_for("setup", course=course))


@app.route("/<course>/hierarchy/<hierarchy>/delete", methods=["POST"])
def hierarchy_delete(course, hierarchy):
    """Delete one of a course's reference hierarchies and everything anchored to
    it: its nodes, the coverage edges into it, per-node attrs, and any
    outline<->reference target rows. The outline isn't deletable here."""
    with db() as conn:
        row = conn.execute(
            "SELECT editable FROM hierarchies WHERE hierarchy=? AND course=?",
            (hierarchy, course)).fetchone()
        if not row:
            abort(404)
        if row["editable"]:
            flash("The course outline can't be deleted here.")
            return redirect(url_for("setup", course=course))
        n = conn.execute("SELECT count(*) FROM coverage WHERE hierarchy=?",
                         (hierarchy,)).fetchone()[0]
        conn.execute("DELETE FROM coverage WHERE hierarchy=?", (hierarchy,))
        conn.execute("DELETE FROM node_attr WHERE hierarchy=?", (hierarchy,))
        conn.execute("DELETE FROM hierarchy_targets WHERE outline=? OR reference=?",
                     (hierarchy, hierarchy))
        conn.execute("DELETE FROM nodes WHERE hierarchy=?", (hierarchy,))
        conn.execute("DELETE FROM hierarchies WHERE hierarchy=?", (hierarchy,))
    flash(f"Deleted hierarchy {hierarchy!r} ({n} coverage edge(s) removed).")
    return redirect(url_for("setup", course=course))


@app.route("/<course>/rename", methods=["POST"])
def course_rename(course):
    """Rename a course's display title (the id/slug is fixed)."""
    title = (request.form.get("title") or "").strip()
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        if not title:
            flash("Title can't be empty.")
            return redirect(url_for("setup", course=course))
        conn.execute("UPDATE courses SET title=? WHERE course=?", (title, course))
    flash(f"Renamed course to {title!r}.")
    return redirect(url_for("setup", course=course))


@app.route("/<course>/delete", methods=["POST"])
def course_delete(course):
    """Delete a course and everything anchored to it: all its hierarchies (+ their
    nodes, coverage, attrs, targets) and its pool membership, then prune any
    objectives left with no course."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM courses WHERE course=?", (course,)).fetchone():
            abort(404)
        hs = [r[0] for r in conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=?", (course,))]
        for h in hs:
            conn.execute("DELETE FROM coverage WHERE hierarchy=?", (h,))
            conn.execute("DELETE FROM node_attr WHERE hierarchy=?", (h,))
            conn.execute("DELETE FROM nodes WHERE hierarchy=?", (h,))
            conn.execute("DELETE FROM hierarchy_targets WHERE outline=? OR reference=?", (h, h))
        conn.execute("DELETE FROM hierarchies WHERE course=?", (course,))
        conn.execute("DELETE FROM course_objectives WHERE course=?", (course,))
        conn.execute("DELETE FROM courses WHERE course=?", (course,))
        # Objectives are course-agnostic (interned by text); drop only those now
        # belonging to no course at all.
        conn.execute("DELETE FROM objectives WHERE uuid NOT IN "
                     "(SELECT uuid FROM course_objectives)")
    flash(f"Deleted course {course!r}.")
    return redirect(url_for("index"))


@app.route("/<course>/bundle")
def course_bundle_download(course):
    """Download the whole course as a single self-contained JSON bundle."""
    with db() as conn:
        try:
            doc = course_bundle.export_course(conn, course)
        except KeyError:
            abort(404)
    payload = json.dumps(doc, indent=2, ensure_ascii=False)
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{course}-course.json"'})


@app.route("/course/import", methods=["POST"])
def course_import():
    """Recreate a course from an uploaded bundle (the inverse of the download)."""
    f = request.files.get("file")
    back = request.referrer or url_for("index")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(back)
    try:
        doc = json.loads(f.read().decode("utf-8", "replace"))
        with db() as conn:
            cid = course_bundle.import_course(conn, doc)
    except Exception as e:  # bad JSON, version, or id/slug clash (rolled back)
        flash(f"Import failed: {e}")
        return redirect(back)
    flash(f"Imported course {cid!r}.")
    return redirect(url_for("tree", course=cid))


def workspace_data(conn, course, H):
    """Node tree of hierarchy H with the raw objectives mapped onto each node, plus
    the unplaced pool. Single placement per hierarchy: an objective sits under one
    node of H or in the pool."""
    objs = {r["uuid"]: {"uuid": r["uuid"], "text": r["text"], "position": r["position"],
                        "node": None}
            for r in conn.execute(
                "SELECT o.uuid, o.text, co.position FROM objectives o "
                "JOIN course_objectives co ON co.uuid=o.uuid AND co.course=? "
                "WHERE o.status='active'", (course,))}
    for r in conn.execute(
        "SELECT cv.uuid, cv.node_id FROM coverage cv "
        "JOIN course_objectives co ON co.uuid=cv.uuid AND co.course=? "
        "WHERE cv.hierarchy=?", (course, H)):
        o = objs.get(r["uuid"])
        if o:
            o["node"] = r["node_id"]

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
    """Two complementary coverage directions for a hierarchy:

    - leaf coverage: of this hierarchy's leaves, how many have >=1 objective
      (what you want ~100% of for a standard like the CED -- every leaf taught);
    - placement: of all the course's raw objectives, how many are placed somewhere
      in this hierarchy (what you want ~100% of for the course outline -- every
      objective has a home).
    """
    leaves = [n for n in nodes if n["is_leaf"]]
    covered = sum(1 for n in leaves if by_node.get(n["node_id"]))
    placed = sum(len(v) for v in by_node.values())  # objectives with a home here
    total = placed + len(pool)                       # all the course's objectives
    return {"leaves": len(leaves), "covered": covered, "gaps": len(leaves) - covered,
            "pct": round(100 * covered / len(leaves)) if leaves else 0,
            "pool": len(pool), "placed": placed, "total": total,
            "placed_pct": round(100 * placed / total) if total else 0}


@app.route("/<course>")
def tree(course):
    """The course landing page is its course outline."""
    return redirect(url_for("plan", course=course))


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
        "workspace.html", course=course, ref=hierarchy,
        page_title=h["title"],
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


# --------------------------------------------------------------------------
# Objectives + mapping (write views)


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
                           hierarchies=cols, total=len(rows),
                           page_title=f"{course.upper()} objectives")


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


@app.route("/<course>/outline.md")
def outline_md(course):
    """Download the course's rendered plan (the deliverable) as markdown -- the
    render_outline script, in the app."""
    with db() as conn:
        data = render_outline.fetch(conn, course)
    md = render_outline.render(course, *data)
    return Response(
        md, mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{course}-plan.md"'})


@app.route("/<course>/objectives/upload", methods=["POST"])
def objectives_upload(course):
    """Upload a file of objectives into the course POOL only (import_objectives):
    plain text, or a TSV with an objective/text column. Any node-id column is
    ignored here -- categorize on a hierarchy page (see hierarchy_upload), where
    the target hierarchy is unambiguous."""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file chosen.")
        return redirect(url_for("objectives", course=course))
    try:
        items, mode = import_objectives.parse_text(f.read().decode("utf-8", "replace"))
    except ValueError as e:
        flash(f"Upload failed: {e}")
        return redirect(url_for("objectives", course=course))
    # Import here is POOL-ONLY: never create coverage edges. Categorizing an
    # objective to a node is done on a hierarchy page, where the target hierarchy
    # is unambiguous -- doing it here made it too easy to hit the wrong one. Strip
    # any node-id column.
    had_nodes = any(n for _, _, n in items)
    items = [(u, t, None) for u, t, n in items]
    _ref, stats, _dangling, _known = import_objectives.load(DB_PATH, course, items)
    msg = (f"Imported {f.filename!r}: read {stats['read']}, "
           f"{stats['objectives_new']} new objectives, {stats['pooled']} added to the pool.")
    if had_nodes:
        msg += " · node-id column ignored — categorize objectives on a hierarchy page."
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
        return render_template("_rawitem.html", o={"uuid": u, "text": text})
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


def _id_list(field="ids"):
    """Read an id list sent either as repeated fields or one comma-joined field."""
    vals = request.form.getlist(field)
    if len(vals) == 1 and "," in vals[0]:
        vals = vals[0].split(",")
    return [v for v in (s.strip() for s in vals) if v]


# --------------------------------------------------------------------------
# The Plan page: Units -> Lessons, with raw objectives placed into them.


@app.route("/<course>/plan")
def plan(course):
    """Back-compat: the plan is now the outline hierarchy's workspace."""
    with db() as conn:
        O = outline_hierarchy(conn, course) or ensure_outline(conn, course)
        conn.commit()
    return redirect(url_for("hierarchy_view", course=course, hierarchy=O))


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
    app.run(debug=True, host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5001")))
