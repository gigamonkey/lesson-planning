# Calendar view

Add a per-course **calendar view** that shows how a course's outline lays out
across the actual school year — taking holidays, breaks, and short weeks into
account — so you can see "how things fit into the year." This mirrors the
calendar in the `bhs-cs` repo, but rendered entirely server-side and driven by
this project's outline hierarchy + the Python `bells` library.

## The model (decided)

- **The course outline is the source of truth** for layout. The calendar reads
  the editable outline hierarchy (units → lessons) and lays it onto the year.
- **Units carry a duration in _weeks_; lessons carry a duration in _days_**
  (default **1 day**, so most lessons need no explicit duration). Units are never
  "mixed" — in the outline it's weeks for units, days for lessons.
- **Weeks are "loose" / calendar weeks, not 5-school-day blocks.** A unit of
  `2 weeks` consumes the next **2 teaching weeks** of the year regardless of how
  many school days each contains. A week with a Monday off is still one week and
  does **not** overflow into the next. Full-week breaks (Thanksgiving, winter
  break, …) are shown between units but don't count against a unit's weeks.
- **Progressive detail.** Early on you may have only units with week counts — the
  calendar shows how those blocks fall across the year. As lessons get filled in,
  the calendar additionally shows how many days of lessons sit inside each unit's
  weeks (and whether they over/underfill the available school days).
- **Durations are first-class on _any_ node**, not just the outline. The IB
  syllabi already tag topics like `## A1 Computer fundamentals (18 hours)`; those
  hours become structured data too. They do **not** drive the calendar in v1, but
  enable a later report comparing time actually scheduled in the outline against
  the suggested hours in a reference syllabus.
- **Calendar data** comes from the `bells` repo's `bhs-calendars/` directory (the
  source of truth). **For now assume every course runs the full school year**
  (`firstDay`…`lastDay`).

## Decisions captured from discussion

1. **Storage:** a new dedicated `node_duration` table (typed amount + unit), not
   `node_attr`.
2. **Markdown syntax:** a trailing parenthetical on the node's heading line —
   `(N weeks)` / `(N days)` / `(N hours)` — matching the IB convention already in
   use. Parsed out of the title, stored separately, re-emitted on save.
3. **Calendar source / year binding:** read calendar JSONs from a directory
   (`LESSON_CALENDAR_DIR`, default the sibling `../bells/bhs-calendars`), and bind
   a course to a year via its `plan.md` front matter (`calendar: bhs-2025-2026`,
   optional `start:`). Full-year assumption for now.
4. **Scope:** v1 schedules the **outline only** (units→weeks, lessons→days).
   Reference hours are parsed/stored/displayed as durations but not auto-scheduled.

---

## Part 1 — Durations as first-class node data

### 1.1 Schema (`schema.sql` + `app.ensure_schema` migration)

```sql
CREATE TABLE IF NOT EXISTS node_duration (
  hierarchy TEXT NOT NULL,
  node_id   TEXT NOT NULL,
  amount    REAL NOT NULL,        -- 2, 0.5, 18
  unit      TEXT NOT NULL,        -- 'week' | 'day' | 'hour'
  PRIMARY KEY (hierarchy, node_id),
  FOREIGN KEY (hierarchy, node_id) REFERENCES nodes(hierarchy, node_id)
);
```

- One duration per node (units aren't mixed → PK on `(hierarchy, node_id)`).
- `amount` is REAL to allow halves (e.g. a half-day lesson) and the IB decimals.
- `unit` is a small enum stored singular; rendering pluralizes.
- Migration: `CREATE TABLE IF NOT EXISTS …` in `ensure_schema`, idempotent.

Add two course-level columns for the year binding (mirrors `primary_outline`):

```sql
ALTER TABLE courses ADD COLUMN calendar   TEXT;  -- e.g. 'bhs-2025-2026'
ALTER TABLE courses ADD COLUMN start_date TEXT;  -- optional ISO date; default = calendar firstDay
```

### 1.2 Markdown syntax (`hierarchy.py`, this repo owns the format)

A node heading may end with **one** duration tag:

```
# Unit: Selection and Iteration (2 weeks)
## Hello, world (3 days)
## A1 Computer fundamentals (18 hours)
## B4 Abstract data types (HL only) (23 hours)   # only the LAST (..) is the tag
```

- `DURATION_RE = r"\s*\((\d+(?:\.\d+)?)\s+(weeks?|days?|hours?)\)\s*$"` — strict, and
  only the **trailing** parenthetical, so titles with incidental parens
  (`(HL only)`) are untouched (same discipline as the objective `(#token)`).
- In `to_nodes`, each node dict gains `duration: {"amount": float, "unit": str}`
  or `None`. The matched tag is **stripped from the node text** so the stored
  title is clean (`A1 Computer fundamentals`, not `… (18 hours)`).
- In `to_markdown`, re-append the tag from the node's duration so references
  round-trip byte-for-byte.
- Unit normalization: store singular (`week`/`day`/`hour`); accept singular or
  plural on input.

### 1.3 Loading references (`load_nodes.py`)

- `to_nodes` now carries `duration`; `build_rows` stays as-is for `nodes`, and a
  parallel `build_durations(slug, nodes)` yields `node_duration` rows.
- `load_into` clears + re-inserts this hierarchy's `node_duration` alongside its
  `nodes` (same scoped-replace pattern), so reloading the IB syllabus populates
  the hours automatically.

### 1.4 Outline round-trip (`plan_io.py`)

- `parse_plan`: extract a trailing duration tag from each `# Unit:` and `## lesson`
  heading (weeks for units, days for lessons), returning it alongside the title.
- `read_course`: write the outline's `node_duration` rows from those.
- `render_course`: re-emit `# Unit: <title> (N weeks)` and `## <title> (N days)`
  from `node_duration` (omit when absent; never emit `(1 day)` for the implicit
  default to keep diffs quiet — only emit a lesson duration when it's ≠ 1).
- `_reference_files`: fetch each reference's `node_duration` and pass to
  `to_markdown` so reference `.md` files round-trip their tags.
- `is_dirty`: unchanged in shape — it already diffs `render_course` output, which
  now includes the tags.

### 1.5 Bundle (`course_bundle.py`)

- Export/import a `node_duration` array (per hierarchy), like `node_attr`. Bump
  `BUNDLE_VERSION` to `1.2.0` (additive; importer ignores it on older bundles).

### 1.6 Tests

- Extend `test_plan_io.py`: a course whose outline + a reference carry durations
  round-trips (write → read → render byte-identical), including the "no `(1 day)`"
  rule and the `(HL only) (23 hours)` case.

---

## Part 2 — The calendar engine (`calendar_view.py`, new module)

A pure, testable module: outline + duration data + a `BellSchedule` → a view
model. No Flask, no SQL (the app hands it plain data).

### 2.1 Loading the school year

- `LESSON_CALENDAR_DIR` (env; default `<repo>/../bells/bhs-calendars`, matching
  the editable `bells` path dependency).
- Resolve the course's calendar id from `courses.calendar`; load
  `<dir>/<id>.json`; build `BellSchedule([data], {"role": "student"})`.
- Span = `start_date or data.firstDay` … `data.lastDay` (full year for now).

### 2.2 Teaching weeks

`bells` has no week concept (confirmed), so group manually:

1. Walk calendar weeks (Monday-anchored) across the span.
2. For each week, collect its **school days** via `bells.is_school_day(d)`.
3. Classify: a week with ≥1 school day is a **teaching week**; a week with 0 is a
   **break** (label it from the calendar's `breakNames` when available).
4. Result: an ordered list of `Week{ index, monday, school_days: [date], is_break,
   break_name }`.

### 2.3 Laying out the outline

Walk units in outline order, consuming teaching weeks from the front of the list:

- Each unit consumes its `weeks` count of **teaching weeks** (breaks encountered
  in between are emitted as vacation rows and do **not** decrement the count) —
  this is the "loose weeks" rule.
- A unit with **no** explicit `weeks`: derive a provisional span by laying its
  lessons' days into school days and rounding up to whole teaching weeks (so an
  un-estimated unit still appears); flag it as derived.
- Within a unit's consumed weeks, lay lessons in order across the available school
  days: each lesson occupies `days` consecutive school days (default 1), recording
  the dates and which week(s) it spans.
- Track **overflow** (lesson-days exceed the unit's school days) and **underflow**
  (school days left unscheduled) per unit, and **year-level** over/underflow
  (total unit weeks vs. teaching weeks available) — surfaced as warnings, like
  bhs-cs.

### 2.4 View model returned

```
CalendarView{
  warnings: [str],
  units: [ Unit{
     title, weeks, derived: bool,
     rows: [ WeekRow{ label, date_range, school_day_count,
                      cells: [ {lesson_title, days, kind: 'lesson'|'free'|'overflow'} ] }
           | BreakRow{ name, date_range } ]
  } ],
}
```

### 2.5 Tests

- `test_calendar_view.py` against a small fixed calendar JSON (or a bhs one):
  units consume the right weeks across a known holiday; a "Monday off" week stays
  one week; lessons fill days; over/underflow flags fire.

---

## Part 3 — App wiring & UI

### 3.1 Front-matter plumbing (`plan_io`, `app`)

- `_emit_front_matter` / `parse_plan` learn `calendar:` and `start:`.
- `read_course` writes `courses.calendar` / `courses.start_date`; `render_course`
  emits them; `course_bundle` carries them.

### 3.2 Route (`app.py`)

- `GET /<course>/calendar` → resolve outline + durations from the db, build the
  `BellSchedule`, call `calendar_view`, render `calendar.html`.
- Graceful states: no `calendar:` bound → a short "pick a calendar" notice; no
  units yet → empty-state hint.

### 3.3 Template (`templates/calendar.html` + CSS)

Server-rendered, modeled on bhs-cs's layout:

- One **section per unit** (header: title + weeks, derived/overflow badges).
- **Week rows**: a left date cell (`Week 5 · Sep 8–12`, school-day count) + a flex
  row of lesson cells sized by `days` (`flex: <days>`); leftover space is a muted
  "free" cell. Break weeks render as a labeled vacation row.
- Year-level warnings as a subtle banner up top (reuse the muted style).

### 3.4 Navigation

- Add **Calendar** to each course's sidebar block (next to "Course outline" /
  "Objectives"), and a header link from the outline workspace.

### 3.5 Editing durations

- Primary path: the **Markdown editor** (the tags round-trip) — works from day one.
- Phase 2 (nice-to-have): inline duration fields on the outline workspace — a
  small `(N weeks)` input on each unit head and `(N days)` on each lesson, autosaved
  to `node_duration` like the existing title/learning-objective fields.

---

## Part 4 — Deferred / future

- **Scheduled-vs-suggested report:** compare the time the outline devotes to each
  topic (outline weeks/days → school days, optionally → hours) against the
  suggested hours on a reference syllabus (IB). The `node_duration` data on both
  the outline and the references is exactly what this needs; the correspondence is
  via the existing `coverage` edges (outline lesson ↔ reference topic) plus
  `hierarchy_targets`.
- **Hours → days scheduling:** convert hour-tagged reference nodes into days (via a
  configurable class-minutes-per-day) to schedule a syllabus directly. Not needed
  while the outline is the source of truth.
- **Partial-year / multi-term courses:** drop the full-year assumption; per-course
  start/end.

---

## Implementation order

1. `node_duration` schema + `ensure_schema` migration; `courses.calendar/start_date`.
2. `hierarchy.py`: `DURATION_RE`, `to_nodes` `duration`, `to_markdown` emit.
3. `load_nodes.py`: populate `node_duration` (reloading the IB syllabus fills hours).
4. `plan_io.py`: outline parse/render/round-trip of tags + front-matter year binding.
5. `course_bundle.py`: carry `node_duration` + course year fields (v1.2.0).
6. `calendar_view.py`: teaching-weeks + layout engine (+ unit tests).
7. `app.py`: `/<course>/calendar` route; sidebar/header link.
8. `templates/calendar.html` + CSS.
9. Docs: `FORMAT.md` (duration syntax, front-matter keys), `CLAUDE.md` (new module,
   `LESSON_CALENDAR_DIR`).
10. Round-trip + calendar-view tests.

Steps 1–5 are the "durations are first-class" foundation and are independently
useful (and independently testable) before any calendar rendering exists.
