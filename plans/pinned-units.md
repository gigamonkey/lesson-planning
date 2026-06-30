# Pinning units to calendar weeks

## Goal

Let a unit be **pinned** to a specific point in the school year — most often an
*end* anchor: a "Review" unit that runs the last few weeks before exams and must
**end on the exam week**. Today the calendar lays units out purely sequentially
(`calendar_view.build_calendar`): each unit consumes the next N school weeks from
a single advancing cursor, so the only way to make a unit land on a given week is
to hand-tune the week counts of *every* unit before it. We want to state the fact
directly — "this unit ends on week 35" — and have the layout engine honor it,
treating **too many units before a pinned unit as overflow** (there is no room to
fit them) and any **slack before a pinned unit as unplanned weeks** (a gap), the
same way it already handles a unit whose lessons overflow its weeks.

A pin is authored in `plan.md` (a tag on the unit heading), persisted in the db
(a small table mirroring `node_duration`), and consumed by the calendar layout
engine. Everything round-trips through the markdown editor; a dedicated UI
affordance for setting a pin is a follow-up (see [UI](#ui-follow-up)).

## What a pin is

A pin attaches a unit to a **school-week number** with an **edge**:

- **`start`** — the unit *begins* on that week.
- **`end`** — the unit *ends* on that week. This is the motivating case.

The week number is the calendar's canonical **bells school-week number** — the
same numbering already shown in the calendar (`_week_badges` / `w["number"]` in
`calendar_view.py`) and used by the AP-exam / grading-period annotations. So a
teacher reads the target week straight off the calendar they're looking at.

### Markdown syntax

A unit heading already may carry a duration tag (`# Unit: Review (3 weeks)`). The
pin is a second trailing tag, placed **last** (after the duration), keyword-led so
it can't be confused with the duration's `weeks`:

```markdown
# Unit: Orientation (1 week) (starts week 1)
# Unit: Review (3 weeks) (ends week 35)
```

Grammar (the pin is the final parenthesized group on a unit heading; the duration,
if present, is the one before it):

```
(pin)      ::= "(" ("starts"|"ends") " week " <int> ")"
```

This mirrors the existing tag vocabulary owned by `hierarchy.py`
(`DURATION_RE`/`split_duration`/`format_duration`) and the lesson identity token,
which is likewise the trailing group. Parse order on a unit heading: strip the pin
off the end first, then the duration, then the title — the inverse of how we emit
them (`title + duration + pin`).

Only **units** take pins. Lessons flow within their unit; references never pin.

### Optional extension: landmark anchors

Because the motivating case is literally "ends on the **exam** week," and the
calendar already derives AP-exam / IB-exam / grading-close weeks from the bells
`annotations` (`_week_badges`, `annotations_for_week`), the same syntax can accept
a **named landmark** instead of a literal number:

```markdown
# Unit: Review (3 weeks) (ends at ap-exams)
```

Resolution maps `ap-exams` / `ib-exams` to the first school-week the corresponding
range annotation overlaps. This is strictly more robust than a literal number
(survives a calendar/year swap), but adds a resolution path and failure mode
("calendar has no apExams annotation"). **Recommendation:** ship literal week
numbers first; landmarks are a clean follow-on within the same grammar and storage
(the stored value is just `"ap-exams"` instead of an int — see below). The plan is
written so literal numbers are self-contained and landmarks slot in later.

## Storage

### On disk

Nothing new beyond the heading tag above. The pin round-trips through `plan.md`
exactly as the duration tag does — it is re-derived from the db on every
`write_course` and re-parsed on every load, so there is no separate file or TSV.

### In the db

Add a dedicated table mirroring `node_duration` (same key shape, same
"one-per-node, regenerated each load" lifecycle):

```sql
-- A unit pinned to a calendar week: the outline layout anchors the unit's START
-- or END on that school-week number instead of flowing it sequentially. Authored
-- as a trailing unit-heading tag, e.g. '# Unit: Review (3 weeks) (ends week 35)'.
-- Only outline units pin; one pin per node. `week` is the bells school-week number
-- (an int); `edge` is 'start' | 'end'. (A future landmark form may store a name in
-- a companion column; see plans/pinned-units.md.)
CREATE TABLE IF NOT EXISTS node_pin (
  course    TEXT    NOT NULL,
  hierarchy TEXT    NOT NULL,         -- bare slug (always the outline)
  node_id   TEXT    NOT NULL,         -- the unit's positional id ('1', '2', ...)
  week      INTEGER NOT NULL,         -- bells school-week number
  edge      TEXT    NOT NULL,         -- 'start' | 'end'
  PRIMARY KEY (course, hierarchy, node_id),
  FOREIGN KEY (course, hierarchy, node_id) REFERENCES nodes(course, hierarchy, node_id)
);
```

Bump `PRAGMA user_version` in `schema.sql` from `2` to `3`. The db is a disposable
cache: the app discards + rebuilds any db whose stamp doesn't match (see the schema
header comment), so no migration is needed — a stale db heals itself on startup.

**Why a table and not `node_attr`?** Units have *positional* node_ids (`"1"`,
`"2"`, regenerated from heading order each load), not stable uuids. That is fine
for the pin because `node_duration` already keys unit durations by exactly the same
positional id and round-trips correctly: both the duration and the pin are parsed
from the heading and re-inserted under whatever positional id the unit has *this*
load, then read back at write time. A dedicated table keeps the parallel with
`node_duration` (typed columns, clear lifecycle) instead of stuffing an encoded
`"end:35"` string into the generic `node_attr` bag.

### For the landmark extension

If/when landmarks land: relax `week` to nullable and add `landmark TEXT` (exactly
one of the two is set), or keep one `target TEXT` column the resolver interprets as
an int or a name. Either is additive; the layout engine consumes a resolved
week-index regardless.

## Parsing & round-trip (`hierarchy.py`, `plan_io.py`)

1. **`hierarchy.py`** — add `PIN_RE`, `split_pin(head)` → `(clean_head, pin)` where
   `pin` is `{"edge": "start"|"end", "week": int}` or `None`, and `format_pin(pin)`
   → `" (ends week 35)"` / `""`. These live beside `split_duration`/
   `format_duration` so the heading-tag vocabulary stays owned in one place. (The
   reference parser `to_nodes` does *not* need pins — references don't pin — so this
   is consumed only from `plan_io`.)

2. **`plan_io.parse_plan`** (`plan_io.py:135`) — on a unit heading, after splitting
   the duration (`plan_io.py:178`), also `split_pin`. The `units` tuple grows from
   `(key, title, duration)` to `(key, title, duration, pin)`.

3. **`plan_io._insert_outline_nodes`** (`plan_io.py:~252`) — alongside
   `_set_duration` add a `_set_pin(conn, course, outline, node_id, pin)` that
   inserts the `node_pin` row. The `units` loop unpacks the extra field.

4. **`plan_io.write_course`** (`plan_io.py:800`) — read pins for the outline into a
   `pin_of` map (like `dur_of`) and append `hierarchy.format_pin(pin_of.get(...))`
   to the unit heading, *after* the duration tag:

   ```python
   out.append(f"# Unit: {u['text']}".rstrip()
              + hierarchy.format_duration(dur_of.get(u["node_id"]))
              + hierarchy.format_pin(pin_of.get(u["node_id"])))
   ```

5. **`plan_io.load_plan_text`** (`plan_io.py:508`) and **`read_course`**
   (`plan_io.py:323`) — add `node_pin` to the table-clear lists
   (`("coverage", "node_attr", "node_duration", "nodes")` →
   `+ "node_pin"`) so a reload rebuilds pins cleanly. `load_plan_text` runs the same
   `parse_plan` → `_insert_outline_nodes` path, so it picks up pins for free once
   the tuple and insert are updated.

6. **`validate.py`** — optional: flag a pin `week` that isn't a positive integer.
   It can't check the week is *in range* without loading the calendar (validation
   runs on raw files, no db), so range/conflict problems surface as calendar
   warnings (below), not validation errors.

## The layout engine (`calendar_view.py`) — the crux

Today `build_calendar` (`calendar_view.py:255`) walks `units` in order with a
single boxed cursor `idx`, calling `emit_unit` → `_consume` to take the next run of
school weeks. Pins turn this single sweep into a **segmented** layout: pinned units
are fixed anchors; the non-pinned units between two anchors are laid into the
**bounded window** between them.

### Inputs

`_outline_units` (in `app.py:1909`) gains a `pin` field per unit:
`{"node_id", "title", "weeks", "lessons", "pin": {"edge","week"} | None}` — read
from `node_pin` next to the existing `node_duration` read.

### Step 1 — resolve each pin to a start index

Build the week list as today (`_weeks`). Map each `week` number to its index in
that list via `{w["number"]: i for i, w in enumerate(weeks) if not w["is_break"]}`.
For each pinned unit compute its **anchor start index** `T`:

- **`start` edge:** `T` = the index of that week.
- **`end` edge:** `T` = walk *backward* from the end week's index, accumulating
  school weeks (skipping break boxes) until the unit's span is covered, where span
  is the explicit `weeks` count, or — for an auto-sized unit — the number of school
  weeks whose school *days* sum to the lessons' total days. Walking backward sizes
  an auto unit against the actual end-of-year weeks without the start⇄span circular
  dependency.

Out-of-range or unresolved (week number past year-end, or break-only) → drop the
pin, emit a warning, fall back to sequential placement for that unit.

### Step 2 — segment and lay out

Process units left to right, maintaining `cursor` (= `idx`). For each unit:

- **Non-pinned:** emit as today, but bounded by the next anchor — pass a
  `max_idx` (the next pinned unit's `T`, or `len(weeks)`) into `_consume`/
  `emit_unit` so the unit cannot consume school weeks past the boundary.
- **Pinned (anchor at `T`):** first reconcile `cursor` with `T`:
  - `cursor < T` → the preceding units finished early: emit the gap `weeks[cursor:T]`
    as an **`unplanned` section** (the existing "Unplanned" pseudo-unit path,
    `calendar_view.py:373`), then set `cursor = T`.
  - `cursor > T` → the preceding units **overran** the pin: they were already
    clipped at `max_idx = T` in the bounded pass above, so their tail lessons
    overflow (existing per-lesson overflow in `emit_unit`, `calendar_view.py:320`)
    and a unit that got **zero** weeks is marked fully overflowed. Set `cursor = T`.
    Add a warning: *"N unit(s)/lesson-day(s) don't fit before pinned unit X."*
  - Emit the pinned unit starting exactly at `T`; advance `cursor` past it.

The trailing run after the last anchor lays out into `[cursor, year-end)` exactly
as today, including the **greedy last-unit** behavior — except a pinned unit is
never greedy (it has a fixed anchor).

### Bounded `_consume`

`_consume` (`calendar_view.py:168`) gains a `max_idx` parameter (default
`len(weeks)`). The loops that do `idx < len(weeks)` instead stop at
`min(len(weeks), max_idx)`. When a bounded unit runs out of weeks before its
lessons fit, the existing fit logic in `emit_unit` (`calendar_view.py:312-325`)
already turns the unplaced lesson-days into `overflow` entries — so unit-before-pin
overflow reuses the lesson-overflow machinery rather than inventing a new one. A
unit that gets **no** weeks at all (`taken == []`) is emitted as an explicit
overflowed stub (title shown, all lessons in `overflow`, zero rows) so the teacher
sees it didn't fit instead of it silently vanishing.

### Warnings

`build_calendar` already returns `warnings`. Add:

- **Overflow before a pin:** units/lessons that couldn't fit in a bounded segment.
- **Unplanned gap before a pin:** when `cursor < T` (slack). Honest, possibly
  intended (the existing tail-unplanned section already models this; here it's
  mid-year). Consider downgrading to an info note rather than a warning.
- **Out-of-range / unresolvable pin:** week past year-end, or lands on a break.
- **Conflicting pins:** a later pin (in outline order) resolving to an index `≤`
  the previous anchor's end. Honor the earlier anchor; the later one's segment is
  empty/overflows. Warn naming both units.

### Touch-ups

- **`_requested_weeks`** (`calendar_view.py:219`) — the "units ask for more weeks
  than the year has" warning is a global sum independent of position, so pins don't
  change it; leave as-is for v1. (A more precise per-segment capacity warning is
  possible later but the overflow-before-pin warning already covers the user-facing
  case.)
- **`_outline_unit_weeks`** (`app.py:1770`) — unchanged: it reads `weeks_shown`
  straight from `build_calendar`'s output, so the outline week pills automatically
  reflect a clipped (overflowed) pinned-segment unit.

## UI (follow-up)

v1 is **markdown-only**: a teacher sets a pin by editing the unit heading in the
"Edit as Markdown" outline editor (`/<course>/outline/source` → `load_plan_text` →
`write_course`), which already round-trips the new tag. The calendar should
**render the pin** — e.g. a small "📌 ends wk 35" badge on the pinned unit's header
and/or the anchored week — so the constraint is visible and overflow/gap warnings
make sense.

A later iteration can add an inline affordance (like the existing duration/week
editing on the outline unit pills, `_outline_units.html`): a "pin to week…" control
posting to a new route that writes `node_pin` and re-renders — mirroring
`node_duration_set`. Out of scope for the first cut.

## Documentation

- **`FORMAT.md`** — under [Durations](FORMAT.md) (§4 of the outline section), add a
  short "Pins" subsection documenting the `(starts|ends week N)` tag, its position
  (last group, after the duration), and that only units take it.
- **`CLAUDE.md`** — extend the calendar paragraph and the `schema.sql` /
  `calendar_view.py` / `plan_io.py` rows to mention `node_pin` and pin-aware
  layout.
- **`schema.sql`** — the `node_pin` table + version bump (above).

## Tests

- **`test_plan_io.py`** — round-trip a `plan.md` whose units carry
  `(starts week N)` / `(ends week N)` (with and without a duration tag); assert the
  `node_pin` rows and the re-emitted markdown match. Cover the parse-order edge:
  a unit with both a duration tag and a pin tag.
- **`test_calendar_view.py`** — pinned-layout cases on a synthetic bells calendar:
  (a) end-pinned unit lands on the target week; (b) too many units before a pin →
  overflow warning + clipped/zeroed units; (c) slack before a pin → unplanned gap;
  (d) start-pin vs end-pin; (e) out-of-range pin → warning + sequential fallback;
  (f) two conflicting pins → warning, earlier wins.
- **`validate.py`** test — a malformed pin week (non-integer) is flagged, if the
  optional validation check is added.

## Open questions

1. **Edges:** support both `start` and `end`, or end-only? (Recommendation: both —
   start is the trivial baseline and end is the motivating case; the storage and
   layout cost is identical.)
2. **Landmarks now or later?** (Recommendation: later — ship literal week numbers
   first; the grammar/storage are forward-compatible.)
3. **Gap-before-pin:** warning, info note, or silent unplanned section?
   (Recommendation: info note — it's often intended slack.)
4. **Overflow policy:** clip-and-overflow the preceding units (this plan), or
   instead *shrink* auto-sized units to fit? (Recommendation: clip-and-overflow —
   it matches the existing lesson-overflow model and keeps authored week counts
   honest rather than silently compressing them.)
</content>
</invoke>
