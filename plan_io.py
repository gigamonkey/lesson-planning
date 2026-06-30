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
    plan.md, so it is NOT duplicated here);
  * `lessons/<slug>-<shortid>.md` -- one file per outline lesson, holding the lesson
    plan's free-text parts (the learning objective and the rest). Identity is the
    file's front-matter `uuid:`, which the plan.md lesson heading's `(#token)`
    resolves to by prefix; the lesson plan is a distillation of the lesson's placed
    objectives, which stay in plan.md.

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
import validate

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
LESSONS_DIR = "lessons"

# A lesson plan's free-text parts, in canonical order: (node_attr name, the `##`
# heading used in the lesson file). Stored one row per non-empty part in node_attr
# -- `learning_objective` is the very attr the outline/calendar already read, so
# moving the learning objective into the lesson file changes only where it is
# authored, not how it is consumed. plan.md no longer carries the LO line.
LESSON_PARTS = [
    ("preview", "Preview"),
    ("learning_objective", "Learning objective"),
    ("review", "Review"),
    ("key_ideas", "Key ideas"),
    ("expert_thinking", "Expert thinking"),
    ("guided_practice", "Guided practice"),
    ("closure", "Closure"),
    ("independent_practice", "Independent practice"),
    ("summation", "Summation"),
]
_PART_BY_HEADING = {disp.lower(): key for key, disp in LESSON_PARTS}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text):
    """A filesystem-friendly slug for a lesson filename: lowercase, runs of
    non-alphanumerics collapsed to one '-', trimmed; "lesson" when empty. Cosmetic
    only -- a lesson's identity is the uuid, not the slug."""
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-") or "lesson"


def lesson_shortid(uuid):
    """The cosmetic uuid fragment in a lesson filename: the first 8 hex chars
    (dashes dropped). Never parsed for identity -- the file's front-matter `uuid:`
    is. Keeps same-titled lessons from colliding on disk."""
    return uuid.replace("-", "")[:8]


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

    units   : [(node_id, title, duration, pin)]  positional ids "1", "2", ...
              `pin` is {"edge": "start"|"end", "week": int} (a unit pinned to a
              calendar week) or None.
    lessons : [(key, parent_unit_id, token|None, title, duration)] -- `key` is a
              positional parse handle ("1.1", "u.1") used only to wire bullets and
              legacy LOs to their lesson; `token` is the heading's identity token
              (the trailing "(#abcd)"), resolved to a stable uuid by the caller.
    los     : {lesson_key: learning_objective_text}   legacy plan.md LO (migrated)
    bullets : [(text, token|None, placement_key|None)] in document order
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
    in_unassigned = False   # inside the "# Unassigned lessons" section (parent = None)

    for line in body.splitlines():
        m = hierarchy.HEADING.match(line)
        if m:
            depth, rest = len(m.group(1)), m.group(2).strip()
            if depth == 1:
                # The unplaced-objectives pool is its own H1 section
                # ("# Unplaced objectives"); bullets under it are pooled, not
                # placed. A following "# Unit:" resets in_pool below.
                if rest.lower().startswith("unplaced"):
                    cur_lesson, in_pool, in_unassigned = None, True, False
                    continue
                # Lessons with no unit get their own H1 section; subsequent "## "
                # headings become lessons with parent = None.
                if rest.lower().startswith("unassigned"):
                    cur_unit, cur_lesson, lesson_n = None, None, 0
                    in_pool, in_unassigned = False, True
                    continue
                um = UNIT_RE.match(rest)
                title = um.group(1) if um else rest
                # The pin tag is the LAST trailing group (strip it first), then the
                # duration tag, then the title.
                title, pin = hierarchy.split_pin(title)
                title, duration = hierarchy.split_duration(title)
                unit_n += 1
                lesson_n = 0
                cur_unit = str(unit_n)
                cur_lesson, in_pool, in_unassigned = None, False, False
                units.append((cur_unit, title, duration, pin))
            elif depth == 2:
                # Legacy: the pool used to be an H2 ("## Pool ..."); still
                # accept it so older plan.md files load (re-render migrates it
                # to the H1 form above).
                if rest.lower().startswith("pool"):
                    cur_lesson, in_pool = None, True
                    continue
                # The lesson heading is "TITLE (dur) (#token)": the identity token
                # is the LAST trailing (#...) group (strip it first), then the
                # duration tag renders just inside it. The positional `key` ("1.1")
                # is a parse handle only -- identity is the uuid the token resolves to.
                head = rest.strip()
                tok = TOKEN_RE.search(head)
                token = tok.group(1) if tok else None
                if tok:
                    head = TOKEN_RE.sub("", head).rstrip()
                title, duration = hierarchy.split_duration(head)
                # 1 day is the implicit default; don't store/round-trip it.
                if duration == {"amount": 1.0, "unit": "day"}:
                    duration = None
                lesson_n += 1
                # Unassigned lessons get a distinct "u.N" key (no parent) so they
                # don't collide with unit ids ("1", "2", ...).
                if in_unassigned:
                    cur_lesson, parent = f"u.{lesson_n}", None
                else:
                    cur_lesson = f"{cur_unit}.{lesson_n}" if cur_unit else str(lesson_n)
                    parent = cur_unit
                in_pool = False
                lessons.append((cur_lesson, parent, token, title, duration))
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


def _set_pin(conn, course, hierarchy, node_id, pin):
    if pin:
        conn.execute("INSERT INTO node_pin(course, hierarchy, node_id, week, edge)"
                     " VALUES (?, ?, ?, ?, ?)",
                     (course, hierarchy, node_id, pin["week"], pin["edge"]))


def _rebuild_outline_nodes(conn, course, outline, units, lessons):
    """Insert the outline's unit nodes (positional ids "1", "2", ...) and lesson
    nodes (stable uuid node_ids), plus any unit/lesson durations and unit pins.
    Lesson CONTENT
    (the node_attr parts, including the learning objective) is applied separately by
    the caller -- loaded from the lesson files (read_course) or preserved across the
    edit (load_plan_text). `lessons` is [(uuid, parent_unit_id, title, duration)].
    The caller has already cleared the outline's nodes/node_attr/node_duration."""
    ordinal = 0
    unit_has_lesson = {u: False for u, _, _, _ in units}
    for _, parent, _, _ in lessons:
        if parent in unit_has_lesson:
            unit_has_lesson[parent] = True
    for uid, utitle, duration, pin in units:
        conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (course, outline, uid, None, "unit",
                      0 if unit_has_lesson[uid] else 1, ordinal, utitle))
        _set_duration(conn, course, outline, uid, duration)
        _set_pin(conn, course, outline, uid, pin)
        ordinal += 1
    for uuid, parent, ltitle, duration in lessons:
        conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (course, outline, uuid, parent, "lesson", 1, ordinal, ltitle))
        _set_duration(conn, course, outline, uuid, duration)
        ordinal += 1


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
    # Surface internal-consistency problems (dangling/garbled uuids) on the raw
    # files before the lenient load below quietly papers over them.
    for prob in validate.validate_course(course_dir):
        print(f"warning: {os.path.basename(os.path.normpath(course_dir))}: validation: {prob}")
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
        for tbl in ("coverage", "node_attr", "node_duration", "node_pin", "nodes",
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

        # Outline hierarchy (editable=1): units (positional) + lessons (uuid nodes).
        conn.execute(
            "INSERT INTO hierarchies(course, hierarchy, editable, title, source)"
            " VALUES (?, ?, 1, ?, ?)",
            (course, outline, "Course outline", os.path.basename(plan_path)))
        # Resolve each lesson heading's token against the lesson files (identity is
        # the file's uuid); a tokenless/new heading mints a fresh uuid. Then load the
        # lesson content into node_attr, seeding the learning-objective part from a
        # legacy plan.md "**Learning objective:**" line when the file lacks it.
        lesson_data = _read_lesson_files(course_dir)
        resolved, key_to_uuid = _resolve_lessons(lessons, lesson_data.keys())
        _rebuild_outline_nodes(conn, course, outline, units, resolved)
        legacy_lo = {key_to_uuid[k]: v for k, v in los.items() if k in key_to_uuid}
        for uuid, _parent, _title, _dur in resolved:
            parts = dict(lesson_data.get(uuid, {}).get("parts", {}))
            if "learning_objective" not in parts and legacy_lo.get(uuid):
                parts["learning_objective"] = legacy_lo[uuid]
            _set_lesson_content(conn, course, outline, uuid, parts)
        # Bullets place objectives onto lessons by the positional parse key; remap to
        # the resolved lesson uuids (a unit-rough placement key stays as the unit id).
        bullets = [(text, token, key_to_uuid.get(pl, pl) if pl else pl)
                   for (text, token, pl) in bullets]

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
        # Lesson identity set the heading tokens resolve against (the course's
        # existing lesson nodes), plus a snapshot of all outline node_attr (lesson
        # content) to restore for lessons that survive the edit -- plan.md is not the
        # source of truth for lesson content, so re-deriving the outline from it must
        # not clobber the parts the lesson files own.
        known_lesson_uuids = [u for (u,) in conn.execute(
            "SELECT node_id FROM nodes WHERE course=? AND hierarchy=? AND level='lesson'",
            (course, outline))]
        saved_attrs = {(nid, name): val for nid, name, val in conn.execute(
            "SELECT node_id, name, value FROM node_attr WHERE course=? AND hierarchy=?",
            (course, outline))}

        # Reset ONLY the outline + the pool; reference hierarchies and their
        # coverage edges are not represented in plan.md, so they stay as they are.
        for tbl in ("coverage", "node_attr", "node_duration", "node_pin", "nodes"):
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

        resolved, key_to_uuid = _resolve_lessons(lessons, known_lesson_uuids)
        _rebuild_outline_nodes(conn, course, outline, units, resolved)
        # Restore preserved content for surviving lessons; migrate a legacy plan.md
        # learning-objective line for any lesson still missing that part.
        new_lesson_uuids = {uuid for uuid, _p, _t, _d in resolved}
        for (nid, name), val in saved_attrs.items():
            if nid in new_lesson_uuids:
                conn.execute("INSERT OR REPLACE INTO node_attr(course, hierarchy, node_id, name, value)"
                             " VALUES (?, ?, ?, ?, ?)", (course, outline, nid, name, val))
        legacy_lo = {key_to_uuid[k]: v for k, v in los.items() if k in key_to_uuid}
        for uuid, lo in legacy_lo.items():
            if uuid in new_lesson_uuids and (uuid, "learning_objective") not in saved_attrs:
                conn.execute("INSERT OR REPLACE INTO node_attr(course, hierarchy, node_id, name, value)"
                             " VALUES (?, ?, ?, 'learning_objective', ?)", (course, outline, uuid, lo))
        bullets = [(text, token, key_to_uuid.get(pl, pl) if pl else pl)
                   for (text, token, pl) in bullets]
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
# lesson files (lessons/<slug>-<shortid>.md): one markdown file per lesson, holding
# the lesson plan's free-text parts. Identity is the front-matter `uuid:`; the body
# is the LESSON_PARTS as `## <heading>` sections. plan.md's heading token resolves
# to one of these by prefix (like an objective bullet resolves against objectives.tsv).

def _parse_lesson_body(body):
    """Split a lesson file body into {part_name: text}. Only the known part
    headings (LESSON_PARTS) delimit sections; any other heading is content of the
    current part, so a part's free text may contain its own sub-headings. Empty
    parts are dropped."""
    parts, cur, buf = {}, None, []
    for line in body.splitlines():
        m = hierarchy.HEADING.match(line)
        if m and len(m.group(1)) == 2:
            key = _PART_BY_HEADING.get(m.group(2).strip().lower())
            if key is not None:
                if cur is not None:
                    parts[cur] = "\n".join(buf).strip()
                cur, buf = key, []
                continue
        if cur is not None:
            buf.append(line)
    if cur is not None:
        parts[cur] = "\n".join(buf).strip()
    return {k: v for k, v in parts.items() if v}


def _read_lesson_files(course_dir):
    """Load `lessons/*.md` into {uuid: {"title": str, "parts": {name: text}}}. A
    missing directory or a file with no front-matter `uuid:` is skipped; returns {}
    when there are no lesson files."""
    d = os.path.join(course_dir, LESSONS_DIR)
    if not os.path.isdir(d):
        return {}
    out = {}
    for fn in sorted(os.listdir(d)):
        path = os.path.join(d, fn)
        if not (fn.endswith(".md") and os.path.isfile(path)):
            continue
        with open(path, encoding="utf-8") as f:
            meta, body = hierarchy.parse_front_matter(f.read())
        uuid = meta.get("uuid")
        if not uuid:
            print(f"warning: lesson file {fn!r} has no 'uuid:' front matter; skipped")
            continue
        out[uuid] = {"title": meta.get("title", ""), "parts": _parse_lesson_body(body)}
    return out


def _render_lesson_file(uuid, title, parts):
    """Serialize one lesson to its file text: front matter (`uuid`, `title`) + each
    non-empty part as a `## <heading>` section, in canonical LESSON_PARTS order."""
    out = ["---", f"uuid: {uuid}", f"title: {title}", "---", ""]
    for key, disp in LESSON_PARTS:
        val = (parts.get(key) or "").strip()
        if val:
            out += [f"## {disp}", "", val, ""]
    return "\n".join(out).rstrip() + "\n"


def _resolve_lessons(parsed_lessons, known_lesson_uuids):
    """Map each parsed lesson to a stable uuid node_id. A heading's identity token is
    resolved by shortest-unique prefix against `known_lesson_uuids` (the lesson files
    in read_course; the course's existing lesson nodes in load_plan_text); a
    tokenless, ambiguous, or already-claimed token mints a fresh uuid (a new lesson).

    Returns (resolved, key_to_uuid): `resolved` is [(uuid, parent, title, duration)]
    in document order; `key_to_uuid` maps each lesson's positional parse key to its
    uuid (to remap bullet placements and legacy learning-objective lines)."""
    known = list(known_lesson_uuids)
    resolved, key_to_uuid, used = [], {}, set()
    for key, parent, token, title, duration in parsed_lessons:
        uuid = resolve_token(token, known) if token else None
        if uuid is None or uuid in used:
            uuid = _new_uuid()
            known.append(uuid)
        used.add(uuid)
        key_to_uuid[key] = uuid
        resolved.append((uuid, parent, title, duration))
    return resolved, key_to_uuid


def _set_lesson_content(conn, course, outline, uuid, parts):
    """Insert a lesson's non-empty parts into node_attr (one row per part). Assumes
    the lesson's node_attr rows were already cleared."""
    for key, _disp in LESSON_PARTS:
        val = (parts.get(key) or "").strip()
        if val:
            conn.execute("INSERT OR REPLACE INTO node_attr(course, hierarchy, node_id, name, value)"
                         " VALUES (?, ?, ?, ?, ?)", (course, outline, uuid, key, val))


# --------------------------------------------------------------------------
# export: database -> directory

def render_course(conn, course):
    """Build a course's authored files in memory (no disk writes): returns
    ({plan.md, objectives.tsv, coverage.tsv, lessons/<slug>-<shortid>.md ...} ->
    text, n_objectives, n_coverage). Raises KeyError if the course is absent.
    write_course writes these to disk."""
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
    pin_of = {r["node_id"]: {"week": r["week"], "edge": r["edge"]}
              for r in conn.execute("SELECT node_id, week, edge FROM node_pin"
                                    " WHERE course=? AND hierarchy=?", (course, outline))}
    # All lesson content (every node_attr part), for the lesson files.
    attrs = {}    # lesson node_id -> {part_name: value}
    for r in conn.execute("SELECT node_id, name, value FROM node_attr"
                          " WHERE course=? AND hierarchy=?", (course, outline)):
        attrs.setdefault(r["node_id"], {})[r["name"]] = r["value"]
    # Stable identity tokens for the lesson headings (shortest unique uuid prefix).
    lesson_tokens = abbrev_tokens([L["node_id"] for L in lessons])
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
    def emit_lesson(L):
        # "## TITLE (dur) (#token)": the title + an optional duration tag + the
        # identity token. The opaque uuid node_id never leaks; only its short token
        # does (resolved back to the uuid via the lesson file on load). The learning
        # objective and the rest of the lesson content live in the lesson file, not
        # here -- plan.md stays the objective-pool canvas.
        tok = lesson_tokens.get(L["node_id"], lesson_shortid(L["node_id"]))
        out.append(f"## {L['text']}".rstrip()
                   + hierarchy.format_duration(dur_of.get(L["node_id"]))
                   + f" (#{tok})")
        out.append("")
        ps = placed.get(L["node_id"], [])   # already in per-node order
        for uuid in ps:
            out.append(bullet(uuid))
        if ps:
            out.append("")

    for u in units:
        # rstrip first (a unit may be untitled), then append any "(N weeks)" tag and
        # then any pin tag (the pin is the last group on the line).
        out.append(f"# Unit: {u['text']}".rstrip()
                   + hierarchy.format_duration(dur_of.get(u["node_id"]))
                   + hierarchy.format_pin(pin_of.get(u["node_id"])))
        out.append("")
        # Unit-level "rough" placements: bullets directly under the unit heading,
        # before its lessons (parse_plan reads these back as placed on the unit).
        rough = placed.get(u["node_id"], [])   # already in per-node order
        for uuid in rough:
            out.append(bullet(uuid))
        if rough:
            out.append("")
        for L in lessons_by_unit.get(u["node_id"], []):
            emit_lesson(L)

    # Lessons not under any unit (parent_id NULL -- the outline's "Unassigned
    # lessons" section) get their own H1 so they round-trip; parse_plan reads them
    # back as lessons with no parent.
    unassigned = lessons_by_unit.get(None, [])
    if unassigned:
        out.append("# Unassigned lessons")
        out.append("")
        for L in unassigned:
            emit_lesson(L)

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

    files = {PLAN_FILE: plan_text, OBJECTIVES_TSV: obj_buf.getvalue(),
             COVERAGE_TSV: cov_buf.getvalue()}
    # One lesson file per lesson, named `lessons/<slug>-<shortid>.md`. Always emitted
    # (even content-less) so the lesson's full uuid persists on disk -- the plan.md
    # token is only a prefix, so without the file a reload could not recover identity.
    for L in lessons:
        uuid = L["node_id"]
        name = f"{LESSONS_DIR}/{slugify(L['text'])}-{lesson_shortid(uuid)}.md"
        files[name] = _render_lesson_file(uuid, L["text"], attrs.get(uuid, {}))
    return (files, len(pool), len(cov))


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
    """Serialize a course's authored state to `course_dir`: plan.md + the two TSVs +
    one `lessons/<slug>-<shortid>.md` per lesson, plus a markdown file for each
    reference hierarchy that isn't already on disk (so the courses directory is
    self-contained / reloadable). An existing reference .md -- hand-authored or
    uploaded, same nodes but different formatting -- is left as is.

    The `lessons/` directory is reconciled: a lesson removed from the outline (or
    renamed, whose new slug yields a new filename) has its stale file deleted.

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
    desired_lessons = set()
    for name, text in files.items():
        path = os.path.join(course_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if name.startswith(LESSONS_DIR + "/"):
            desired_lessons.add(os.path.basename(name))
    # Reconcile lessons/: drop files for lessons no longer rendered (a delete, or the
    # old name left behind by a retitle whose slug changed the filename).
    ldir = os.path.join(course_dir, LESSONS_DIR)
    if os.path.isdir(ldir):
        for fn in os.listdir(ldir):
            if (fn.endswith(".md") and fn not in desired_lessons
                    and os.path.isfile(os.path.join(ldir, fn))):
                os.remove(os.path.join(ldir, fn))
                print(f"note: {course!r}: removed stale lesson file {LESSONS_DIR}/{fn}")
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
    for tbl in ("coverage", "node_attr", "node_pin", "nodes"):
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
