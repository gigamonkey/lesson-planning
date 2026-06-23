"""Read and write a course as a directory of markdown + two normalized TSVs.

This is the storage half of "markdown is the fundamental on-disk form of a
course" (see `plans/markdown-as-storage.md` and `FORMAT.md`). A course directory
holds:

  * one or more REFERENCE hierarchy markdown files (csa/csp/ib/book flavor) --
    load-only inputs, never rewritten here (loaded via load_nodes);
  * the OUTLINE `plan.md` (course flavor) -- the one hierarchy this module writes,
    whose front matter also carries the course-level wiring (course id, title,
    primary_reference, primary_outline, targets);
  * `objectives.tsv` (uuid, text) -- the full uuid<->text registry for the pool;
  * `coverage.tsv` (uuid, hierarchy_id, node_id) -- the many-to-many coverage
    edges into the REFERENCE hierarchies (outline placement is structural in
    plan.md, so it is NOT duplicated here).

`read_course` loads such a directory into the database; `write_course` serializes
a course back out to one. The corpus root is both the load source and the export
target, so the pair round-trips. Objective identity rides on a short uuid token
`(#abcd)` on each bullet, resolved by prefix against `objectives.tsv` -- never a
full uuid in the markdown. See FORMAT.md for the format.
"""

import csv
import io
import os
import re
import sqlite3

import hierarchy
import load_nodes

# A trailing identity token on an objective bullet: " (#abcd)". Recognized only as
# the LAST parenthesized group of hex with a '#' sigil, so literal parens in the
# objective text are never mistaken for it.
TOKEN_RE = re.compile(r"\s*\(#([0-9a-fA-F]+)\)\s*$")
LO_RE = re.compile(r"^\*\*Learning objective:\*\*\s*(.*)$")
LESSON_RE = re.compile(r"^(\S+)(?:\s+(.*))?$")   # "1.1 Title" -> id, title
TOKEN_FLOOR = 4   # shortest token length, to limit diff churn / accidental clashes

PLAN_FILE = "plan.md"
OBJECTIVES_TSV = "objectives.tsv"
COVERAGE_TSV = "coverage.tsv"
OUTLINE_KIND = "course-outline"


# --------------------------------------------------------------------------
# uuid tokens

def abbrev_tokens(uuids, floor=TOKEN_FLOOR):
    """Map each uuid to its shortest prefix that is unique among `uuids`.

    Falls back to the whole uuid if even that is not unique (duplicate uuids
    shouldn't happen). Never shorter than `floor`.
    """
    uuids = list(uuids)
    out = {}
    for u in uuids:
        length = min(floor, len(u))
        while length < len(u) and sum(1 for v in uuids if v[:length] == u[:length]) > 1:
            length += 1
        out[u] = u[:length]
    return out


def resolve_token(token, uuids):
    """The unique uuid in `uuids` that starts with `token`, or None if 0 or >1."""
    matches = [u for u in uuids if u.startswith(token.lower())]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------
# front matter (a comma-separated `targets:` list rides the scalar parser)

def _split_list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _emit_front_matter(meta):
    lines = ["---"]
    for key in ("course", "title", "primary_reference", "primary_outline", "targets"):
        val = meta.get(key)
        if val:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# parsing plan.md

def parse_plan(text):
    """Parse a plan.md into (meta, units, lessons, los, bullets).

    units   : [(node_id, title)]                positional ids "1", "2", ...
    lessons : [(node_id, parent_unit_id, title)] positional ids "1.1", "1.2", ...
    los     : {lesson_id: learning_objective_text}
    bullets : [(text, token|None, placement_lesson_id|None)] in document order
              (placement None == pooled / not placed in a lesson)
    """
    meta, body = hierarchy.parse_front_matter(text)
    units, lessons, los, bullets = [], [], {}, []
    unit_n = 0
    lesson_n = 0
    cur_unit = None      # current unit node_id
    cur_lesson = None    # current lesson node_id (None inside the pool / no lesson)
    in_pool = False

    for line in body.splitlines():
        m = hierarchy.HEADING.match(line)
        if m:
            depth, rest = len(m.group(1)), m.group(2).strip()
            if depth == 1:
                um = hierarchy.UNIT.match(rest)
                title = um.group(2) if um else rest
                unit_n += 1
                lesson_n = 0
                cur_unit = str(unit_n)
                cur_lesson, in_pool = None, False
                units.append((cur_unit, title))
            elif depth == 2:
                if rest.lower().startswith("pool"):
                    cur_lesson, in_pool = None, True
                    continue
                lm = LESSON_RE.match(rest)
                title = (lm.group(2) or "").strip() if lm else rest
                lesson_n += 1
                cur_lesson = f"{cur_unit}.{lesson_n}" if cur_unit else str(lesson_n)
                in_pool = False
                lessons.append((cur_lesson, cur_unit, title))
            continue
        lo = LO_RE.match(line)
        if lo and cur_lesson:
            los[cur_lesson] = lo.group(1).strip()
            continue
        b = hierarchy.OBJECTIVE_BULLET.match(line)
        if b:
            raw = b.group(1)
            tok = TOKEN_RE.search(raw)
            token = tok.group(1) if tok else None
            otext = TOKEN_RE.sub("", raw).strip() if tok else raw.strip()
            # In a lesson -> that lesson; under a unit before any lesson -> the unit
            # (rough); in the pool section (or before any unit) -> unplaced.
            placement = None if in_pool else (cur_lesson or cur_unit)
            bullets.append((otext, token, placement))
    return meta, units, lessons, los, bullets


# --------------------------------------------------------------------------
# load: directory -> database

def _md_files(course_dir):
    return sorted(f for f in os.listdir(course_dir)
                  if f.endswith(".md") and os.path.isfile(os.path.join(course_dir, f)))


def _slug_of(path, meta):
    return meta.get("slug") or os.path.splitext(os.path.basename(path))[0]


def read_course(db_path, course_dir):
    """Load one course directory into the database. Idempotent: the course's
    existing hierarchies/coverage/pool are cleared first, then rebuilt from disk.

    Returns (course, n_refs, n_objectives) for reporting.
    """
    # Find the plan file (the .md whose front matter carries a `course:` key) and
    # classify the rest as reference hierarchies.
    plan_path, refs = None, []
    for fn in _md_files(course_dir):
        path = os.path.join(course_dir, fn)
        with open(path, encoding="utf-8") as f:
            meta, _ = hierarchy.parse_front_matter(f.read())
        if "course" in meta:
            plan_path = path
        else:
            refs.append(path)
    if not plan_path:
        raise ValueError(f"no plan.md (a .md with a 'course:' front-matter key) in {course_dir}")

    with open(plan_path, encoding="utf-8") as f:
        meta, units, lessons, los, bullets = parse_plan(f.read())
    course = meta["course"]
    title = meta.get("title") or course.upper()
    # The outline slug comes from the front matter (course-scoped, e.g. "csa-plan"),
    # NOT the plan's filename -- every plan is named plan.md, so the filename would
    # give every course the same slug "plan" (a hierarchies PK collision) and rename
    # the outline on each round-trip.
    outline = meta.get("primary_outline") or _slug_of(plan_path, meta)
    targets = _split_list(meta.get("targets"))

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_DDL)
        # Scoped reset: drop everything owned by this course, then rebuild.
        hs = [h for (h,) in conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=?", (course,))] + [outline]
        ph = ",".join("?" * len(hs))
        for tbl in ("coverage", "node_attr", "nodes"):
            conn.execute(f"DELETE FROM {tbl} WHERE hierarchy IN ({ph})", hs)
        conn.execute(f"DELETE FROM hierarchy_targets WHERE outline IN ({ph})", hs)
        conn.execute("DELETE FROM course_objectives WHERE course=?", (course,))
        conn.execute("DELETE FROM hierarchies WHERE course=?", (course,))
        conn.execute(
            "INSERT INTO courses(course, title, primary_reference, primary_outline)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(course) DO UPDATE SET title=excluded.title,"
            " primary_reference=excluded.primary_reference, primary_outline=excluded.primary_outline",
            (course, title, meta.get("primary_reference"), outline))

        # Reference hierarchies (editable=0), parsed straight from markdown.
        n_refs = 0
        for path in refs:
            with open(path, encoding="utf-8") as f:
                doc = load_nodes.parse(f.read())
            slug = doc.get("slug") or os.path.splitext(os.path.basename(path))[0]
            rows = load_nodes.build_rows(slug, doc["nodes"])
            load_nodes.load_into(conn, slug, course, doc.get("kind") or "reference",
                                 title, rows, source=os.path.basename(path),
                                 title=doc.get("title"))
            n_refs += 1

        # Outline hierarchy (editable=1): units + lessons as positional nodes.
        conn.execute(
            "INSERT INTO hierarchies(hierarchy, course, kind, editable, title, source)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (outline, course, OUTLINE_KIND, "Course outline", os.path.basename(plan_path)))
        ordinal = 0
        unit_has_lesson = {u: False for u, _ in units}
        for _, parent, _ in lessons:
            if parent in unit_has_lesson:
                unit_has_lesson[parent] = True
        for uid, utitle in units:
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (outline, uid, None, "unit", 0 if unit_has_lesson[uid] else 1,
                          ordinal, utitle))
            ordinal += 1
        for lid, parent, ltitle in lessons:
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (outline, lid, parent, "lesson", 1, ordinal, ltitle))
            ordinal += 1
            if los.get(lid):
                conn.execute("INSERT INTO node_attr(hierarchy, node_id, name, value)"
                             " VALUES (?, ?, 'learning_objective', ?)", (outline, lid, los[lid]))

        # Objectives: seed the uuid<->text registry, then resolve each bullet's
        # token (by prefix) to a uuid, or intern by text / mint a fresh uuid.
        reg = _read_objectives_tsv(course_dir)        # [(uuid, text)]
        for uuid, text in reg:
            conn.execute("INSERT OR IGNORE INTO objectives(uuid, text) VALUES (?, ?)",
                         (uuid, text))
        # Resolve tokens against THIS course's registry only (tokens are the
        # shortest prefix unique within the course); newly minted uuids join it.
        known_uuids = [u for u, _ in reg]
        pos = 0
        outline_pos = {}   # outline node_id -> next per-node coverage position
        for otext, token, placement in bullets:
            uuid = resolve_token(token, known_uuids) if token else None
            if uuid:
                # Token wins: markdown text is the source of truth -> adopt it.
                conn.execute("UPDATE objectives SET text=? WHERE uuid=?", (otext, uuid))
            else:
                row = conn.execute("SELECT uuid FROM objectives WHERE text=?", (otext,)).fetchone()
                if row:
                    uuid = row[0]
                else:
                    uuid = _new_uuid()
                    conn.execute("INSERT INTO objectives(uuid, text) VALUES (?, ?)", (uuid, otext))
                    known_uuids.append(uuid)
            conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid, position)"
                         " VALUES (?, ?, ?)", (course, uuid, pos))
            pos += 1
            if placement:
                # The bullet order within a lesson/unit is the per-node order.
                p = outline_pos.get(placement, 0)
                outline_pos[placement] = p + 1
                conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id, position)"
                             " VALUES (?, ?, ?, ?)", (outline, uuid, placement, p))

        # Reference coverage edges (many-to-many across hierarchies). The row order
        # within each (hierarchy, node_id) in coverage.tsv is the per-node order.
        ref_pos = {}
        for uuid, hid, node_id in _read_coverage_tsv(course_dir):
            p = ref_pos.get((hid, node_id), 0)
            ref_pos[(hid, node_id)] = p + 1
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id, position)"
                         " VALUES (?, ?, ?, ?)", (hid, uuid, node_id, p))

        # Outline -> reference targets.
        for ref in targets:
            conn.execute("INSERT OR IGNORE INTO hierarchy_targets(outline, reference)"
                         " VALUES (?, ?)", (outline, ref))
        conn.commit()
        n_obj = conn.execute("SELECT count(*) FROM course_objectives WHERE course=?",
                             (course,)).fetchone()[0]
    finally:
        conn.close()
    return course, n_refs, n_obj


def _read_objectives_tsv(course_dir):
    path = os.path.join(course_dir, OBJECTIVES_TSV)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [(r["uuid"], r["text"]) for r in reader if r.get("uuid")]


def _read_coverage_tsv(course_dir):
    path = os.path.join(course_dir, COVERAGE_TSV)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [(r["uuid"], r["hierarchy_id"], r["node_id"]) for r in reader if r.get("uuid")]


def _new_uuid():
    import uuid as uuidlib
    return str(uuidlib.uuid4())


# --------------------------------------------------------------------------
# export: database -> directory

def render_course(conn, course):
    """Build a course's authored files in memory (no disk writes): returns
    ({plan.md, objectives.tsv, coverage.tsv} -> text, n_objectives, n_coverage).
    Raises KeyError if the course is absent. write_course writes these; is_dirty
    compares them to disk."""
    conn.row_factory = sqlite3.Row
    crow = conn.execute("SELECT course, title, primary_reference, primary_outline"
                        " FROM courses WHERE course=?", (course,)).fetchone()
    if not crow:
        raise KeyError(course)
    outline = crow["primary_outline"] or _first_outline(conn, course)

    units = conn.execute("SELECT node_id, text FROM nodes WHERE hierarchy=? AND level='unit'"
                        " ORDER BY ordinal", (outline,)).fetchall()
    lessons = conn.execute("SELECT node_id, parent_id, text FROM nodes WHERE hierarchy=?"
                         " AND level='lesson' ORDER BY ordinal", (outline,)).fetchall()
    los = {r["node_id"]: r["value"] for r in conn.execute(
        "SELECT node_id, value FROM node_attr WHERE hierarchy=? AND name='learning_objective'",
        (outline,))}
    placed = {}   # node_id -> [uuid], in per-node coverage.position order
    for r in conn.execute("SELECT uuid, node_id, position FROM coverage WHERE hierarchy=?"
                          " ORDER BY position, uuid", (outline,)):
        placed.setdefault(r["node_id"], []).append(r["uuid"])

    pool = conn.execute("SELECT co.uuid, co.position, o.text FROM course_objectives co"
                      " JOIN objectives o ON o.uuid=co.uuid WHERE co.course=?"
                      " ORDER BY co.position, o.text", (course,)).fetchall()
    text_of = {r["uuid"]: r["text"] for r in pool}
    tokens = abbrev_tokens([r["uuid"] for r in pool])

    def bullet(uuid):
        return f"- {text_of.get(uuid, '')}  (#{tokens.get(uuid, uuid[:TOKEN_FLOOR])})"

    meta = {"course": course, "title": crow["title"],
            "primary_reference": crow["primary_reference"],
            "primary_outline": outline,
            "targets": ", ".join(r["reference"] for r in conn.execute(
                "SELECT reference FROM hierarchy_targets WHERE outline=? ORDER BY reference",
                (outline,)))}

    out = [_emit_front_matter(meta), ""]
    lessons_by_unit = {}
    for L in lessons:
        lessons_by_unit.setdefault(L["parent_id"], []).append(L)
    for i, u in enumerate(units, 1):
        out.append(f"# Unit {i}: {u['text']}")
        out.append("")
        # Unit-level "rough" placements: bullets directly under the unit heading,
        # before its lessons (parse_plan reads these back as placed on the unit).
        rough = placed.get(u["node_id"], [])   # already in per-node order
        for uuid in rough:
            out.append(bullet(uuid))
        if rough:
            out.append("")
        for j, L in enumerate(lessons_by_unit.get(u["node_id"], []), 1):
            # Positional id ("1.1"), matching what parse_plan recomputes on read --
            # the db node_id is an opaque uuid and must not leak into the markdown.
            out.append(f"## {i}.{j} {L['text']}".rstrip())
            out.append("")
            if los.get(L["node_id"]):
                out.append(f"**Learning objective:** {los[L['node_id']]}")
                out.append("")
            ps = placed.get(L["node_id"], [])   # already in per-node order
            for uuid in ps:
                out.append(bullet(uuid))
            if ps:
                out.append("")

    placed_uuids = {u for us in placed.values() for u in us}
    unplaced = [r["uuid"] for r in pool if r["uuid"] not in placed_uuids]
    if unplaced:
        out.append("## Pool — not yet placed")
        out.append("")
        for uuid in unplaced:
            out.append(bullet(uuid))
        out.append("")

    plan_text = "\n".join(out).rstrip() + "\n"

    # objectives.tsv (uuid, text), sorted by uuid for a stable diff.
    obj_buf = io.StringIO()
    w = csv.writer(obj_buf, delimiter="\t", lineterminator="\n")
    w.writerow(["uuid", "text"])
    for r in sorted(pool, key=lambda r: r["uuid"]):
        w.writerow([r["uuid"], r["text"]])

    # coverage.tsv (uuid, hierarchy_id, node_id) -- reference edges only (outline
    # placement is structural in plan.md). Ordered by (hierarchy, node_id, position)
    # so the row order WITHIN each node encodes the per-node objective order (read
    # back by encounter order); stable across saves until the order changes.
    cov = conn.execute(
        "SELECT cv.uuid, cv.hierarchy, cv.node_id FROM coverage cv"
        " JOIN course_objectives co ON co.uuid=cv.uuid AND co.course=?"
        " WHERE cv.hierarchy<>? ORDER BY cv.hierarchy, cv.node_id, cv.position, cv.uuid",
        (course, outline)).fetchall()
    cov_buf = io.StringIO()
    w = csv.writer(cov_buf, delimiter="\t", lineterminator="\n")
    w.writerow(["uuid", "hierarchy_id", "node_id"])
    for r in cov:
        w.writerow([r["uuid"], r["hierarchy"], r["node_id"]])

    return ({PLAN_FILE: plan_text, OBJECTIVES_TSV: obj_buf.getvalue(),
             COVERAGE_TSV: cov_buf.getvalue()}, len(pool), len(cov))


def _reference_files(conn, course):
    """{<slug>.md: markdown} for each reference hierarchy, serialized from its db
    nodes so the corpus is self-contained (reloadable). Skips a hierarchy whose
    flavor to_markdown can't represent (e.g. bulleted 'course')."""
    conn.row_factory = sqlite3.Row
    files = {}
    for h in conn.execute("SELECT hierarchy, kind, title FROM hierarchies "
                          "WHERE course=? AND editable=0", (course,)).fetchall():
        rows = [dict(r) for r in conn.execute(
            "SELECT node_id, level, text FROM nodes WHERE hierarchy=? ORDER BY ordinal, node_id",
            (h["hierarchy"],))]
        if not rows:
            continue
        try:
            files[f"{h['hierarchy']}.md"] = hierarchy.to_markdown(
                rows, title=h["title"], kind=h["kind"])
        except ValueError:
            continue
    return files


def write_course(db_path, course, course_dir):
    """Serialize a course's authored state to `course_dir`: plan.md + the two TSVs,
    plus a markdown file for each reference hierarchy that isn't already on disk
    (so the corpus is self-contained / reloadable). An existing reference .md --
    hand-authored or uploaded, same nodes but different formatting -- is left as is.

    Returns (plan_path, n_objectives, n_coverage).
    """
    os.makedirs(course_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        files, n_obj, n_cov = render_course(conn, course)
        ref_files = _reference_files(conn, course)
    finally:
        conn.close()
    for name, text in files.items():
        with open(os.path.join(course_dir, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)
    for name, text in ref_files.items():
        path = os.path.join(course_dir, name)
        if not os.path.exists(path):   # don't churn an existing reference file
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
    return os.path.join(course_dir, PLAN_FILE), n_obj, n_cov


def is_dirty(conn, course, course_dir):
    """True if the course has unsaved changes: the authored files (plan.md + TSVs)
    differ from what write_course would produce, or any reference hierarchy has no
    .md on disk yet. (References are read-only, so once present they're in sync --
    an existence check keeps this cheap, no per-render serialization.) False when
    the course is absent or fully in sync."""
    try:
        files, _n_obj, _n_cov = render_course(conn, course)
    except KeyError:
        return False
    for name, text in files.items():
        try:
            with open(os.path.join(course_dir, name), encoding="utf-8", newline="") as f:
                if f.read() != text:
                    return True
        except FileNotFoundError:
            return True
    for (slug,) in conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0", (course,)):
        if not os.path.exists(os.path.join(course_dir, f"{slug}.md")):
            return True
    return False


def import_structure(conn, outline, reference):
    """Rebuild `outline`'s structure from the first two levels of `reference`.

    Each level-1 (root) node of the reference becomes a unit; each of its level-2
    children becomes a lesson; and every objective covering a lesson's subtree (the
    level-2 node or any descendant) is placed into that lesson. An objective
    covering a unit node directly is placed "rough" on the unit. Unit/lesson titles
    are the reference node's first text line (render adds the "Unit N:" prefix).

    The outline is single-placement per objective (see app.place), so an objective
    is placed in only the FIRST lesson (document order) whose subtree covers it.
    Within each lesson, objectives are ordered (coverage.position) by the reading
    order of their covering nodes in the reference -- so the lesson reads in CED
    order. The master pool order (course_objectives.position) is left untouched;
    the two orders are independent.

    The outline's existing nodes, learning objectives, and placements are cleared
    first. Returns (n_units, n_lessons, n_placed).
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT node_id, parent_id, ordinal, text FROM nodes WHERE hierarchy=?",
        (reference,)).fetchall()
    children = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r)
    for kids in children.values():
        kids.sort(key=lambda r: (r["ordinal"], r["node_id"]))
    title_of = {r["node_id"]: (r["text"] or "").split("\n", 1)[0] for r in rows}

    def subtree(node_id):
        ids = [node_id]
        for c in children.get(node_id, []):
            ids.extend(subtree(c["node_id"]))
        return ids

    cov = {}   # reference node_id -> [uuid], in the reference's per-node order
    for r in conn.execute("SELECT uuid, node_id FROM coverage WHERE hierarchy=?"
                          " ORDER BY position, uuid", (reference,)):
        cov.setdefault(r["node_id"], []).append(r["uuid"])

    # Replace the outline's structure wholesale.
    conn.execute("DELETE FROM coverage WHERE hierarchy=?", (outline,))
    conn.execute("DELETE FROM node_attr WHERE hierarchy=?", (outline,))
    conn.execute("DELETE FROM nodes WHERE hierarchy=?", (outline,))

    placed = set()   # global: each objective lands in exactly one lesson/unit
    node_pos = {}    # outline node_id -> next per-node coverage position
    ordinal = 0
    n_units = n_lessons = n_placed = 0

    def place(node_id, uuids):
        nonlocal n_placed
        for u in uuids:
            if u in placed:
                continue
            placed.add(u)
            p = node_pos.get(node_id, 0)
            node_pos[node_id] = p + 1
            conn.execute("INSERT OR IGNORE INTO coverage(hierarchy, uuid, node_id, position)"
                         " VALUES (?, ?, ?, ?)", (outline, u, node_id, p))
            n_placed += 1

    for unit in children.get(None, []):
        uid = _new_uuid()
        lessons = children.get(unit["node_id"], [])
        conn.execute("INSERT INTO nodes(hierarchy, node_id, parent_id, level, is_leaf,"
                     " ordinal, text) VALUES (?, ?, NULL, 'unit', ?, ?, ?)",
                     (outline, uid, 0 if lessons else 1, ordinal, title_of.get(unit["node_id"], "")))
        ordinal += 1
        n_units += 1
        for lesson in lessons:
            lid = _new_uuid()
            conn.execute("INSERT INTO nodes(hierarchy, node_id, parent_id, level, is_leaf,"
                         " ordinal, text) VALUES (?, ?, ?, 'lesson', 1, ?, ?)",
                         (outline, lid, uid, ordinal, title_of.get(lesson["node_id"], "")))
            ordinal += 1
            n_lessons += 1
            uuids = [u for nid in subtree(lesson["node_id"]) for u in cov.get(nid, [])]
            place(lid, uuids)
        # Objectives mapped straight onto the unit node (not under any lesson) go
        # rough on the unit.
        place(uid, cov.get(unit["node_id"], []))

    return n_units, n_lessons, n_placed


def _first_outline(conn, course):
    row = conn.execute("SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1"
                      " ORDER BY (kind=?) DESC, hierarchy LIMIT 1",
                      (course, OUTLINE_KIND)).fetchone()
    return row[0] if row else course + "-plan"


# The schema this module needs present (a superset is fine). Applied with IF NOT
# EXISTS so read_course works against a fresh db without a separate schema step.
SCHEMA_DDL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "schema.sql")).read()
