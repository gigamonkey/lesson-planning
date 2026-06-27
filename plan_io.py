"""Read and write a course as a directory of markdown + two normalized TSVs.

This is the storage half of "markdown is the fundamental on-disk form of a
course" (see `plans/markdown-as-storage.md` and `FORMAT.md`). A course directory
holds:

  * one or more REFERENCE hierarchy markdown files (each pins a bare, course-
    relative `slug:`) -- load-only inputs, never rewritten here (loaded via
    load_nodes);
  * the OUTLINE `plan.md` (units -> lessons -> objective bullets) -- the one
    hierarchy this module writes, whose front matter also carries the course-level
    wiring (course id, title, primary_outline, targets);
  * `objectives.tsv` (uuid, text) -- the full uuid<->text registry for the pool;
  * `coverage.tsv` (uuid, hierarchy_id, node_id) -- the many-to-many coverage
    edges into the REFERENCE hierarchies (outline placement is structural in
    plan.md, so it is NOT duplicated here).

`read_course` loads such a directory into the database; `write_course` serializes
a course back out to one. The courses root is both the load source and the export
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
# A unit heading is "Unit: TITLE" -- the positional number is not written (it is
# regenerated on load). A legacy "Unit N: TITLE" is still accepted, its number
# discarded. Anything else is taken verbatim as the title.
UNIT_RE = re.compile(r"^Unit(?:\s+\d+)?:\s*(.*)$")   # (.*): a unit may be untitled
TOKEN_FLOOR = 4   # shortest token length, to limit diff churn / accidental clashes

PLAN_FILE = "plan.md"
OBJECTIVES_TSV = "objectives.tsv"
COVERAGE_TSV = "coverage.tsv"


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
    for key in ("course", "title", "primary_outline", "calendar", "targets"):
        val = meta.get(key)
        if val:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# parsing plan.md

def parse_plan(text):
    """Parse a plan.md into (meta, units, lessons, los, bullets).

    units   : [(node_id, title, duration)]       positional ids "1", "2", ...
    lessons : [(node_id, parent_unit_id, title, duration)] ids "1.1", "1.2", ...
    los     : {lesson_id: learning_objective_text}
    bullets : [(text, token|None, placement_lesson_id|None)] in document order
              (placement None == pooled / not placed in a lesson)

    A unit/lesson `duration` is {"amount", "unit"} from a trailing heading tag
    ("(2 weeks)" / "(3 days)"), or None.
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
                # The unplaced-objectives pool is its own H1 section
                # ("# Unplaced objectives"); bullets under it are pooled, not
                # placed. A following "# Unit:" resets in_pool below.
                if rest.lower().startswith("unplaced"):
                    cur_lesson, in_pool = None, True
                    continue
                um = UNIT_RE.match(rest)
                title = um.group(1) if um else rest
                title, duration = hierarchy.split_duration(title)
                unit_n += 1
                lesson_n = 0
                cur_unit = str(unit_n)
                cur_lesson, in_pool = None, False
                units.append((cur_unit, title, duration))
            elif depth == 2:
                # Legacy: the pool used to be an H2 ("## Pool ..."); still
                # accept it so older plan.md files load (re-render migrates it
                # to the H1 form above).
                if rest.lower().startswith("pool"):
                    cur_lesson, in_pool = None, True
                    continue
                # The lesson heading is the title alone; its positional id ("1.1")
                # is regenerated below, not parsed from the markdown.
                title, duration = hierarchy.split_duration(rest.strip())
                # 1 day is the implicit default; don't store/round-trip it.
                if duration == {"amount": 1.0, "unit": "day"}:
                    duration = None
                lesson_n += 1
                cur_lesson = f"{cur_unit}.{lesson_n}" if cur_unit else str(lesson_n)
                in_pool = False
                lessons.append((cur_lesson, cur_unit, title, duration))
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


def _set_duration(conn, course, hierarchy, node_id, duration):
    if duration:
        conn.execute("INSERT INTO node_duration(course, hierarchy, node_id, amount, unit)"
                     " VALUES (?, ?, ?, ?, ?)",
                     (course, hierarchy, node_id, duration["amount"], duration["unit"]))


def _rebuild_outline_nodes(conn, course, outline, units, lessons, los):
    """Insert the outline's unit + lesson nodes (positional ids "1", "1.1", ...),
    each lesson's learning-objective attr, and any unit/lesson durations. The caller
    has already cleared the outline's nodes/node_attr/node_duration. Shared by
    read_course and load_plan_text."""
    ordinal = 0
    unit_has_lesson = {u: False for u, _, _ in units}
    for _, parent, _, _ in lessons:
        if parent in unit_has_lesson:
            unit_has_lesson[parent] = True
    for uid, utitle, duration in units:
        conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (course, outline, uid, None, "unit",
                      0 if unit_has_lesson[uid] else 1, ordinal, utitle))
        _set_duration(conn, course, outline, uid, duration)
        ordinal += 1
    for lid, parent, ltitle, duration in lessons:
        conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (course, outline, lid, parent, "lesson", 1, ordinal, ltitle))
        ordinal += 1
        if los.get(lid):
            conn.execute("INSERT INTO node_attr(course, hierarchy, node_id, name, value)"
                         " VALUES (?, ?, ?, 'learning_objective', ?)",
                         (course, outline, lid, los[lid]))
        _set_duration(conn, course, outline, lid, duration)


def _resolve_bullets(conn, course, outline, bullets, known_uuids, canon=None):
    """Resolve each plan.md bullet to an objective and (re)build the course's pool
    membership/order + outline placements. Each bullet's token is matched (by
    shortest-unique prefix) against `known_uuids` -- the course's existing
    objective registry; a token win adopts the bullet text (markdown is the source
    of truth), while a tokenless/ambiguous bullet interns by text or mints a fresh
    uuid (appended to `known_uuids`). Shared by read_course and load_plan_text.

    `canon` (optional) maps a disk uuid to the canonical uuid the course actually
    uses, so a token that resolves to a loser uuid (a same-text duplicate or a
    cross-course re-mint -- see read_course) lands on the surviving objective.

    Returns the number of outline placements made.
    """
    canon = canon or {}
    pos = 0
    n_place = 0
    outline_pos = {}   # outline node_id -> next per-node coverage position
    for otext, token, placement in bullets:
        uuid = resolve_token(token, known_uuids) if token else None
        if uuid:
            uuid = canon.get(uuid, uuid)
            # Token wins: markdown text is the source of truth -> adopt it.
            conn.execute("UPDATE objectives SET text=? WHERE uuid=?", (otext, uuid))
        else:
            row = conn.execute("SELECT uuid FROM objectives WHERE course=? AND text=?",
                               (course, otext)).fetchone()
            if row:
                uuid = row[0]
            else:
                uuid = _new_uuid()
                conn.execute("INSERT INTO objectives(uuid, course, text) VALUES (?, ?, ?)",
                             (uuid, course, otext))
                known_uuids.append(uuid)
        conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid, position)"
                     " VALUES (?, ?, ?)", (course, uuid, pos))
        pos += 1
        if placement:
            # The bullet order within a lesson/unit is the per-node order.
            p = outline_pos.get(placement, 0)
            outline_pos[placement] = p + 1
            conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position)"
                         " VALUES (?, ?, ?, ?, ?)", (course, outline, uuid, placement, p))
            n_place += 1
    return n_place


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
    # The outline slug is course-relative (bare, normally "plan") -- from the
    # front matter's primary_outline, else the plan's filename stem.
    outline = meta.get("primary_outline") or _slug_of(plan_path, meta)
    targets = _split_list(meta.get("targets"))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA_DDL)
        # Under FK enforcement courses.primary_outline must not dangle: null it
        # before we drop this course's hierarchies, and re-point it once the outline
        # hierarchy has been rebuilt (at the end of the load).
        conn.execute("UPDATE courses SET primary_outline=NULL WHERE course=?", (course,))
        # Scoped reset: every hierarchy-scoped table carries `course`, so dropping
        # this course's rows is a flat per-table delete. Children before parents
        # (coverage/attrs/durations before nodes; nodes before hierarchies) so the
        # deletes themselves satisfy the foreign keys.
        for tbl in ("coverage", "node_attr", "node_duration", "nodes",
                    "hierarchy_targets", "course_objectives", "objectives", "hierarchies"):
            conn.execute(f"DELETE FROM {tbl} WHERE course=?", (course,))
        conn.execute(
            "INSERT INTO courses(course, title, primary_outline, calendar)"
            " VALUES (?, ?, NULL, ?) ON CONFLICT(course) DO UPDATE SET title=excluded.title,"
            " primary_outline=NULL, calendar=excluded.calendar",
            (course, title, meta.get("calendar")))

        # Reference hierarchies (editable=0), parsed straight from markdown.
        n_refs = 0
        seen = {}   # bare slug -> filename, to catch in-course slug collisions
        for path in refs:
            with open(path, encoding="utf-8") as f:
                ref_text = f.read()
            doc = load_nodes.parse(ref_text)
            stem = os.path.splitext(os.path.basename(path))[0]
            slug = doc.get("slug") or stem
            if doc.get("slug") and doc["slug"] != stem:
                print(f"warning: {os.path.basename(path)!r} pins slug {slug!r}; rename "
                      f"the file to {slug}.md (the slug is identity -- don't edit it)")
            if slug in seen:
                raise ValueError(f"two hierarchies in {course!r} resolve to slug {slug!r}: "
                                 f"{seen[slug]} and {os.path.basename(path)}")
            seen[slug] = os.path.basename(path)
            rows = load_nodes.build_rows(course, slug, doc["nodes"])
            durations = load_nodes.build_durations(course, slug, doc["nodes"])
            load_nodes.load_into(conn, slug, course, title, rows,
                                 source=os.path.basename(path),
                                 title=doc.get("title"), durations=durations,
                                 source_md=ref_text)
            n_refs += 1

        # Outline hierarchy (editable=1): units + lessons as positional nodes.
        conn.execute(
            "INSERT INTO hierarchies(course, hierarchy, editable, title, source)"
            " VALUES (?, ?, 1, ?, ?)",
            (course, outline, "Course outline", os.path.basename(plan_path)))
        _rebuild_outline_nodes(conn, course, outline, units, lessons, los)

        # Objectives: seed the uuid<->text registry, then resolve each bullet's
        # token (by prefix) to a uuid, or intern by text / mint a fresh uuid.
        reg = _read_objectives_tsv(course_dir)        # [(uuid, text)]
        # Build `canon`: every disk uuid -> the canonical uuid this course uses.
        # Two TSV rows with identical text are the SAME objective minted twice --
        # only a branch merge produces this, and UNIQUE(course, text) is the model's
        # invariant -- so collapse them onto one winner (the first, i.e. smallest
        # uuid, since the TSV is uuid-sorted) and rewrite every reference (bullets,
        # reference coverage, pool membership) to it. Separately, a uuid already
        # owned by ANOTHER course (a courses directory saved while objectives were still
        # shared) is re-minted, never shared. Both rewrites flow through `canon`.
        canon = {}
        winner_for_text = {}
        dup_losers = {}   # winner uuid -> [loser uuids], for reporting
        for uuid, text in reg:
            win = winner_for_text.get(text)
            if win is not None:
                canon[uuid] = win
                dup_losers.setdefault(win, []).append(uuid)
                continue
            owner = conn.execute("SELECT course FROM objectives WHERE uuid=?", (uuid,)).fetchone()
            use = _new_uuid() if owner and owner[0] != course else uuid
            canon[uuid] = use
            winner_for_text[text] = use
            conn.execute("INSERT OR IGNORE INTO objectives(uuid, course, text) VALUES (?, ?, ?)",
                         (use, course, text))
        for win, losers in dup_losers.items():
            wtext = conn.execute("SELECT text FROM objectives WHERE uuid=?", (win,)).fetchone()[0]
            print(f"note: {course!r}: unified {len(losers) + 1} objectives with identical "
                  f"text onto one uuid ({len(losers)} duplicate(s) merged): {wtext!r}")
        # Resolve tokens against the full disk uuid list (so a loser's token still
        # resolves), then map the result through `canon`.
        _resolve_bullets(conn, course, outline, bullets, [u for u, _ in reg], canon)

        # Reference coverage edges (many-to-many across hierarchies). The row order
        # within each (hierarchy, node_id) in coverage.tsv is the per-node order.
        ref_pos = {}
        for uuid, hid, node_id in _read_coverage_tsv(course_dir):
            uuid = canon.get(uuid, uuid)
            p = ref_pos.get((hid, node_id), 0)
            ref_pos[(hid, node_id)] = p + 1
            conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position)"
                         " VALUES (?, ?, ?, ?, ?)", (course, hid, uuid, node_id, p))

        # Outline -> reference targets, ordered. Every loaded reference is a target
        # (references == the course's ordered reference list): keep the plan.md
        # `targets:` order for those listed, then append any reference it omits.
        ordered = [t for t in targets if t in seen]
        ordered += [s for s in seen if s not in ordered]
        for pos, ref in enumerate(ordered):
            conn.execute("INSERT OR IGNORE INTO hierarchy_targets"
                         "(course, outline, reference, position) VALUES (?, ?, ?, ?)",
                         (course, outline, ref, pos))

        # Re-point the outline now that its hierarchy + nodes exist (see the null
        # above): a no-op when unchanged, but required under FK enforcement.
        conn.execute("UPDATE courses SET primary_outline=? WHERE course=?", (outline, course))

        # (2) Surface placements the merge collapsed onto one objective. After
        # unification an objective may sit in two outline nodes -- each user placed
        # "their" copy in a different lesson. We keep BOTH edges (the objective then
        # shows up under both lessons, which is itself the surfacing), but warn: the
        # outline is single-placement, so a human must pick one. (Normal, unmerged
        # data is single-placement, so this never fires.)
        title_of = dict(conn.execute(
            "SELECT node_id, text FROM nodes WHERE course=? AND hierarchy=?", (course, outline)))
        for (uuid,) in conn.execute(
                "SELECT uuid FROM coverage WHERE course=? AND hierarchy=?"
                " GROUP BY uuid HAVING count(*) > 1", (course, outline)).fetchall():
            where = [title_of.get(nid, nid) for (nid,) in conn.execute(
                "SELECT node_id FROM coverage WHERE course=? AND hierarchy=? AND uuid=?"
                " ORDER BY position", (course, outline, uuid))]
            otext = conn.execute("SELECT text FROM objectives WHERE uuid=?", (uuid,)).fetchone()[0]
            print(f"warning: {course!r}: objective {otext!r} is placed in {len(where)} "
                  f"lessons after merge ({'; '.join(where)}) -- pick one")

        conn.commit()
        n_obj = conn.execute("SELECT count(*) FROM course_objectives WHERE course=?",
                             (course,)).fetchone()[0]
    finally:
        conn.close()
    return course, n_refs, n_obj


def load_plan_text(db_path, course, text):
    """Load an edited plan.md *text* into the database -- the in-memory counterpart
    to read_course's plan half. It rebuilds ONLY the outline hierarchy
    (units/lessons + learning-objective attrs), the course's objective pool
    (membership + order), and the outline placements, resolving each bullet's
    identity token against the course's EXISTING objectives. Reference hierarchies
    and their coverage edges are left untouched -- those live in the reference .md
    files / coverage.tsv, not in plan.md.

    The course must already exist (this edits its outline). Returns
    (n_objectives, n_placements). Raises ValueError if the front matter is missing,
    names a different course, or the course is unknown.
    """
    meta, units, lessons, los, bullets = parse_plan(text)
    if "course" not in meta:
        raise ValueError("plan front matter is missing a 'course:' key")
    if meta["course"] != course:
        raise ValueError(
            f"front-matter course {meta['course']!r} does not match {course!r}")
    targets = _split_list(meta.get("targets"))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA_DDL)
        row = conn.execute(
            "SELECT primary_outline, title FROM courses WHERE course=?",
            (course,)).fetchone()
        if not row:
            raise ValueError(f"unknown course {course!r}")
        cur_outline, cur_title = row
        outline = meta.get("primary_outline") or cur_outline or "plan"
        title = meta.get("title") or cur_title or course.upper()

        # The token registry is the course's CURRENT pool (what render_course
        # computed the tokens against). Capture it before we clear the pool.
        known_uuids = [u for (u,) in conn.execute(
            "SELECT uuid FROM course_objectives WHERE course=? ORDER BY position", (course,))]

        # Reset ONLY the outline + the pool; reference hierarchies and their
        # coverage edges are not represented in plan.md, so they stay as they are.
        for tbl in ("coverage", "node_attr", "node_duration", "nodes"):
            conn.execute(f"DELETE FROM {tbl} WHERE course=? AND hierarchy=?", (course, outline))
        conn.execute("DELETE FROM hierarchy_targets WHERE course=? AND outline=?",
                     (course, outline))
        conn.execute("DELETE FROM course_objectives WHERE course=?", (course,))

        # Ensure the outline hierarchy exists BEFORE pointing courses.primary_outline
        # at it (FK enforcement); it normally already exists.
        conn.execute(
            "INSERT OR IGNORE INTO hierarchies(course, hierarchy, editable, title, source)"
            " VALUES (?, ?, 1, ?, ?)",
            (course, outline, "Course outline", PLAN_FILE))
        conn.execute(
            "UPDATE courses SET title=?, primary_outline=?, calendar=? WHERE course=?",
            (title, outline, meta.get("calendar"), course))

        _rebuild_outline_nodes(conn, course, outline, units, lessons, los)
        n_place = _resolve_bullets(conn, course, outline, bullets, known_uuids)
        # Ordered targets: the edited plan.md order for listed references, then any
        # of the course's existing references it omits (references == targets).
        refs = [r[0] for r in conn.execute(
            "SELECT hierarchy FROM hierarchies WHERE course=? AND editable=0 ORDER BY hierarchy",
            (course,))]
        ordered = [t for t in targets if t in refs]
        ordered += [s for s in refs if s not in ordered]
        for pos, ref in enumerate(ordered):
            conn.execute("INSERT OR IGNORE INTO hierarchy_targets"
                         "(course, outline, reference, position) VALUES (?, ?, ?, ?)",
                         (course, outline, ref, pos))
        conn.commit()
        n_obj = conn.execute("SELECT count(*) FROM course_objectives WHERE course=?",
                             (course,)).fetchone()[0]
    finally:
        conn.close()
    return n_obj, n_place


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
    Raises KeyError if the course is absent. write_course writes these to disk."""
    conn.row_factory = sqlite3.Row
    crow = conn.execute("SELECT course, title, primary_outline, calendar"
                        " FROM courses WHERE course=?", (course,)).fetchone()
    if not crow:
        raise KeyError(course)
    outline = crow["primary_outline"] or _first_outline(conn, course)

    units = conn.execute("SELECT node_id, text FROM nodes WHERE course=? AND hierarchy=?"
                        " AND level='unit' ORDER BY ordinal", (course, outline)).fetchall()
    lessons = conn.execute("SELECT node_id, parent_id, text FROM nodes WHERE course=?"
                         " AND hierarchy=? AND level='lesson' ORDER BY ordinal",
                         (course, outline)).fetchall()
    dur_of = {r["node_id"]: {"amount": r["amount"], "unit": r["unit"]}
              for r in conn.execute("SELECT node_id, amount, unit FROM node_duration"
                                    " WHERE course=? AND hierarchy=?", (course, outline))}
    los = {r["node_id"]: r["value"] for r in conn.execute(
        "SELECT node_id, value FROM node_attr WHERE course=? AND hierarchy=?"
        " AND name='learning_objective'", (course, outline))}
    placed = {}   # node_id -> [uuid], in per-node coverage.position order
    for r in conn.execute("SELECT uuid, node_id, position FROM coverage WHERE course=?"
                          " AND hierarchy=? ORDER BY position, uuid", (course, outline)):
        placed.setdefault(r["node_id"], []).append(r["uuid"])

    pool = conn.execute("SELECT co.uuid, co.position, o.text FROM course_objectives co"
                      " JOIN objectives o ON o.uuid=co.uuid WHERE co.course=?"
                      " ORDER BY co.position, o.text", (course,)).fetchall()
    text_of = {r["uuid"]: r["text"] for r in pool}
    tokens = abbrev_tokens([r["uuid"] for r in pool])

    def bullet(uuid):
        return f"- {text_of.get(uuid, '')}  (#{tokens.get(uuid, uuid[:TOKEN_FLOOR])})"

    meta = {"course": course, "title": crow["title"],
            "primary_outline": outline,
            "calendar": crow["calendar"],
            "targets": ", ".join(r["reference"] for r in conn.execute(
                "SELECT reference FROM hierarchy_targets WHERE course=? AND outline=?"
                " ORDER BY position, reference", (course, outline)))}

    out = [_emit_front_matter(meta), ""]
    lessons_by_unit = {}
    for L in lessons:
        lessons_by_unit.setdefault(L["parent_id"], []).append(L)
    for u in units:
        # rstrip first (a unit may be untitled), then append any "(N weeks)" tag.
        out.append(f"# Unit: {u['text']}".rstrip() + hierarchy.format_duration(dur_of.get(u["node_id"])))
        out.append("")
        # Unit-level "rough" placements: bullets directly under the unit heading,
        # before its lessons (parse_plan reads these back as placed on the unit).
        rough = placed.get(u["node_id"], [])   # already in per-node order
        for uuid in rough:
            out.append(bullet(uuid))
        if rough:
            out.append("")
        for L in lessons_by_unit.get(u["node_id"], []):
            # Title only -- the positional id ("1.1") is regenerated by parse_plan
            # on read, so it (like the opaque db node_id) never leaks into markdown.
            out.append(f"## {L['text']}".rstrip() + hierarchy.format_duration(dur_of.get(L["node_id"])))
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
        out.append("# Unplaced objectives")
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
        " WHERE cv.course=? AND cv.hierarchy<>?"
        " ORDER BY cv.hierarchy, cv.node_id, cv.position, cv.uuid",
        (course, outline)).fetchall()
    cov_buf = io.StringIO()
    w = csv.writer(cov_buf, delimiter="\t", lineterminator="\n")
    w.writerow(["uuid", "hierarchy_id", "node_id"])
    for r in cov:
        w.writerow([r["uuid"], r["hierarchy"], r["node_id"]])

    return ({PLAN_FILE: plan_text, OBJECTIVES_TSV: obj_buf.getvalue(),
             COVERAGE_TSV: cov_buf.getvalue()}, len(pool), len(cov))


def _reference_files(conn, course):
    """{<slug>.md: markdown} for each reference hierarchy, from its stored verbatim
    source markdown, so the courses directory is self-contained (reloadable). A reference is
    load-only, so its markdown is replayed exactly as loaded -- not reconstructed
    from the db nodes. Skips a reference with no stored source (e.g. one created by
    an older import); such a hierarchy has no canonical markdown to emit."""
    conn.row_factory = sqlite3.Row
    files = {}
    for h in conn.execute("SELECT hierarchy, source_md FROM hierarchies "
                          "WHERE course=? AND editable=0", (course,)).fetchall():
        if h["source_md"]:
            files[f"{h['hierarchy']}.md"] = h["source_md"]
    return files


def write_course(db_path, course, course_dir):
    """Serialize a course's authored state to `course_dir`: plan.md + the two TSVs,
    plus a markdown file for each reference hierarchy that isn't already on disk
    (so the courses directory is self-contained / reloadable). An existing reference .md --
    hand-authored or uploaded, same nodes but different formatting -- is left as is.

    Returns (plan_path, n_objectives, n_coverage).
    """
    os.makedirs(course_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
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


def import_structure(conn, course, outline, reference):
    """Rebuild `outline`'s structure from the first two levels of `reference`
    (both bare slugs in `course`).

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
        "SELECT node_id, parent_id, ordinal, text FROM nodes WHERE course=? AND hierarchy=?",
        (course, reference)).fetchall()
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
    for r in conn.execute("SELECT uuid, node_id FROM coverage WHERE course=? AND hierarchy=?"
                          " ORDER BY position, uuid", (course, reference)):
        cov.setdefault(r["node_id"], []).append(r["uuid"])

    # Replace the outline's structure wholesale.
    for tbl in ("coverage", "node_attr", "nodes"):
        conn.execute(f"DELETE FROM {tbl} WHERE course=? AND hierarchy=?", (course, outline))

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
            conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position)"
                         " VALUES (?, ?, ?, ?, ?)", (course, outline, u, node_id, p))
            n_placed += 1

    for unit in children.get(None, []):
        uid = _new_uuid()
        lessons = children.get(unit["node_id"], [])
        conn.execute("INSERT INTO nodes(course, hierarchy, node_id, parent_id, level, is_leaf,"
                     " ordinal, text) VALUES (?, ?, ?, NULL, 'unit', ?, ?, ?)",
                     (course, outline, uid, 0 if lessons else 1, ordinal,
                      title_of.get(unit["node_id"], "")))
        ordinal += 1
        n_units += 1
        for lesson in lessons:
            lid = _new_uuid()
            conn.execute("INSERT INTO nodes(course, hierarchy, node_id, parent_id, level, is_leaf,"
                         " ordinal, text) VALUES (?, ?, ?, ?, 'lesson', 1, ?, ?)",
                         (course, outline, lid, uid, ordinal,
                          title_of.get(lesson["node_id"], "")))
            ordinal += 1
            n_lessons += 1
            uuids = [u for nid in subtree(lesson["node_id"]) for u in cov.get(nid, [])]
            place(lid, uuids)
        # Objectives mapped straight onto the unit node (not under any lesson) go
        # rough on the unit.
        place(uid, cov.get(unit["node_id"], []))

    return n_units, n_lessons, n_placed


def _first_outline(conn, course):
    """Fallback outline slug when courses.primary_outline is unset: the course's
    (normally only) editable hierarchy, else the conventional 'plan'."""
    row = conn.execute("SELECT hierarchy FROM hierarchies WHERE course=? AND editable=1"
                      " ORDER BY hierarchy LIMIT 1", (course,)).fetchone()
    return row[0] if row else "plan"


# The schema this module needs present (a superset is fine). Applied with IF NOT
# EXISTS so read_course works against a fresh db without a separate schema step.
SCHEMA_DDL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "schema.sql")).read()
