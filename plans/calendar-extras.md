# Calendar extras: exam days, AP exam weeks, grading periods

Augment the [calendar view](calendar.md) so it shows the same year-context the
`bhs-cs` calendar shows: **exam days** (mid-year and end-of-year exam weeks
rendered as distinct cells, not as bookable lesson days), **AP exam weeks** (the
early-May window flagged on the weeks it overlaps), and **grading-period-close
labels** (e.g. "Q1 close", "S1 close" on the week a marking period ends). This is
the information `bhs-cs` surfaces, ported to this project's server-rendered,
`bells`-driven calendar.

**Scope: BHS calendars only.** We only need to handle the `bhs-*` calendars, so
this doesn't have to generalize across the other schools' calendars.

## What bhs-cs does (the reference)

`bhs-cs` (`modules/calendar.ts`, `client-js/js/calendar-builder.ts`) gets this
from two places:

- **Exam days** come from the **bells calendar data itself** — the
  `nonClassDays` object, keyed by date with a label:

  ```json
  "nonClassDays": {
    "2025-12-17": "exam",
    "2025-12-18": "exam",
    "2025-12-19": "exam",
    "2026-06-01": "exam",
    "2026-06-02": "exam",
    "2026-06-03": "exam",
    "2026-06-04": "bonus"
  }
  ```

  It filters `nonClassDays` for the label `"exam"` into a set, and any such day
  renders as an "Exams" box (`.scheduled.assessment`) instead of a lesson slot.

- **AP exam weeks and grading periods** come from a **local sidecar**,
  `modules/year-config.json`, keyed by school year — *not* from the bells data,
  which has neither:

  ```json
  "2025-2026": {
    "gradingPeriods": { "5": "Q1 progress", "9": "Q1", "18": "S1", ... },
    "apExams": { "start": "2026-05-04", "end": "2026-05-15" }
  }
  ```

  bhs-cs merges this over the bells year data (`{ ...bhsYear, ...extras }`), then
  marks any week containing a day in `[start, end]` with `isAP = true` and adds
  an "AP exams" label to that week's date cell, and labels the week whose number
  matches a `gradingPeriods` key with "`<name>` close" (grading periods are keyed
  by **week number**, e.g. week 9 → "Q1 close").

The lesson for us: **exams need no new data** — the BHS bells JSONs we already
read carry `nonClassDays`, and the Python `bells` library already exposes it (you
already added the exam `nonClassDays` to both `bhs-2025-2026.json` and
`bhs-2026-2027.json`). **AP exam weeks and grading periods are the genuinely new
data**, and a small sidecar is the right home for them (the bells JSONs are owned
by the `bells` repo; we shouldn't edit them, and neither BHS file carries
`apExams` or `gradingPeriods`).

## Current state in this repo

- `bells.BellSchedule` already parses `nonClassDays` and exposes
  `non_class_label(date) -> str | None` (`bells/calendar.py:441`). The raw dict
  is also on `data["nonClassDays"]`.
- **Exam days are silently treated as ordinary school days.**
  `bells.is_school_day` (`calendar.py:385`) is `True` for an exam day (it's a
  weekday and not in `holidays`), so `calendar_view._weeks` counts it as a normal
  school day and `_week_cells` paints it `free` or fills it with a lesson. The
  exam label is never consulted.
- `calendar_view.build_calendar` receives both `bs` and the raw `data`, so both
  the library API and the raw dict are already in hand — no plumbing needed to
  reach the exam labels.
- The calendar dir defaults to the **bells checkout itself** (`app.py:56`,
  `../bells/bhs-calendars`) and is read live at request time — there is no copy in
  this repo, so your edits to `bhs-2025-2026.json` / `bhs-2026-2027.json` are
  already in effect with no recopy needed.
- There is no AP-exam or grading-period data anywhere, and no sidecar mechanism.

## The model (proposed)

### Exam days — read from bells, render as their own cell kind

Exam days stay **part of the teaching week** (so the week still appears and still
counts toward unit week-budgets — an exam week is a real week of the year) but
are **removed from the lesson-assignment pool** so lessons flow *around* them.
This is cleaner than the bhs-cs behavior, where lessons can be "consumed" by exam
chunks; here an exam day simply isn't a slot a lesson can land on.

Concretely, treat any day with a non-class label as a labeled special day:

- A new cell kind `"exam"` (label `"exam"`) renders as a distinct box (red/amber,
  like bhs-cs's `.assessment`), spanning consecutive exam days the way lessons do.
- Other labels (e.g. `"bonus"`) render generically as a `"special"` cell carrying
  the label text, so we don't hard-code a closed set. (v1 can style only `exam`
  specially and show the rest as a neutral labeled cell.)

### AP exam weeks + grading periods — small sidecar, keyed by calendar id

Add a sidecar that augments a bells calendar with the two things the bells repo
doesn't carry. Keyed by **calendar id** (the same id a course binds to via the
`calendar:` front-matter key), so the data is defined once per year and shared by
every course on that calendar — AP exams and grading periods are properties of
the *year*, not the course.

A new env-configurable directory in *this* repo, defaulting to a committed
`calendar-extras/` folder:

```
calendar-extras/bhs-2025-2026.json
```

```json
{
  "apExams": { "start": "2026-05-04", "end": "2026-05-15" },
  "gradingPeriods": { "5": "Q1 progress", "9": "Q1", "14": "S1 progress", "18": "S1" }
}
```

- `apExams: {start, end}` — the AP exam window. Any **teaching week** overlapping
  `[start, end]` is flagged `is_ap` and rendered with an "AP exams" badge in its
  date column. AP exam days remain ordinary school days (at BHS, AP exams happen
  during the day but school continues), so they are *not* pulled from the lesson
  pool — only the week is badged.
- `gradingPeriods: {week-number: name}` — maps a **teaching-week number** (the
  same 1..n numbering `_weeks` already assigns) to a marking-period name. The week
  with that number is labeled "`<name>` close" in its date column. Keying by week
  number matches bhs-cs and sidesteps having to pin grading periods to exact
  dates.

Exam days need **no sidecar** — they come straight from each BHS calendar's own
`nonClassDays` (already present in both year files). The sidecar is optional: a
calendar with no extras file behaves exactly as today, plus exam rendering from
its own `nonClassDays`.

## Implementation

### 1. `calendar_view.py` — load + merge the sidecar

In `load_calendar(calendar_id, calendar_dir, extras_dir=None)`:

- After loading the bells JSON, look for `<extras_dir>/<calendar_id>.json`. If
  present, copy its `apExams` and `gradingPeriods` onto the returned `data`
  (e.g. `data["apExams"] = extras.get("apExams")`). No merge into `nonClassDays`
  is needed — exams already live in the bells file.
- Return `(BellSchedule, data)` as today; callers gain AP/grading info via `data`.

Keep `calendar_view` pure (no Flask) — the app passes `extras_dir` in.

### 2. `calendar_view.py` — exam-aware layout

- **`_weeks` / week grouping:** unchanged — exam days are school days and stay in
  their teaching week (so week numbering and counts are unaffected).
- **`_week_cells(week, assign, labels)`:** consult a `labels` map
  (`date -> non_class_label`). For a school day with a label, emit a cell of kind
  `"exam"` (or `"special"` for other labels) carrying the label, instead of
  `free`/`lesson`. Consecutive same-label days merge into one spanning block, like
  lessons do.
- **`build_calendar` / `emit_unit`:** when building the assignable `sdays` list,
  **exclude labeled days** so lessons are never assigned onto an exam day. Build
  `labels` once from `bs.non_class_label` (or `data["nonClassDays"]`) and thread
  it into `_week_cells`. Overflow accounting already keys off `sdays`, so exam
  days correctly reduce the school days a unit's lessons can fill.

### 3. `calendar_view.py` — AP exam weeks + grading periods

- Parse `data.get("apExams")` into `start`/`end` dates (helper like the existing
  `_d`).
- When emitting a week row, set `is_ap = any(start <= d <= end for d in week["days"])`.
- Look up `grading = data.get("gradingPeriods", {}).get(str(week["number"]))` and,
  if set, put it on the row as `grading_close`.
- Include both on the row dict
  (`{"kind": "week", ..., "is_ap": True/False, "grading_close": name_or_None}`).

### 4. `templates/_calendar_content.html` + `templates/calendar.html` (CSS)

- **Exam cells:** in the `row.cells` loop, an `exam` cell gets its own class and a
  label ("Exams"); a `special` cell shows its label text. Add CSS next to the
  existing `.cal-cell.lesson/.free/.off` rules — e.g.
  `.cal-cell.exam { background:#fdecea; border:1px solid #f5c2c0; color:#b02a37; }`.
- **Week badges:** in the `cal-when` column, when `row.is_ap` add
  `<br><span class="cal-ap">AP exams</span>`, and when `row.grading_close` add
  `<br><span class="cal-gp">{{ row.grading_close }} close</span>` (both mirror
  bhs-cs's "AP exams" / "Q1 close" labels). Add small `.cal-ap` / `.cal-gp`
  styles.

### 5. `app.py`

- Add `CALENDAR_EXTRAS_DIR = os.environ.get("LESSON_CALENDAR_EXTRAS_DIR", <repo>/calendar-extras)`
  next to the existing `CALENDAR_DIR`.
- Pass it through in `_calendar_ctx` →
  `calendar_view.load_calendar(cal_id, CALENDAR_DIR, CALENDAR_EXTRAS_DIR)`.
- The htmx weeks-pill path (`_calendar_content.html`) already routes through
  `_calendar_ctx`, so it picks up exams/AP/grading automatically.

### 6. Seed data + docs

- Commit `calendar-extras/bhs-2025-2026.json` and `calendar-extras/bhs-2026-2027.json`
  with each year's real AP window and grading periods (pull the dates/week numbers
  from the school calendar; bhs-cs's `year-config.json` has the 2025-2026 values
  to copy).
- Document the `calendar-extras/` convention and the `apExams`/`gradingPeriods`
  sidecar keys in `FORMAT.md` (near the `calendar:` front-matter docs) and the
  `Calendar` section of `CLAUDE.md`.

## Tests (`test_calendar_view.py`)

- A week containing Dec 17–19 renders those three days as `exam` cells, not
  `free`/`lesson`, and a lesson placed in that week flows onto the week's
  non-exam school days only.
- Exam days don't change teaching-week counts or unit week-budgets.
- With `apExams: {2026-05-04, 2026-05-15}`, exactly the weeks overlapping that
  window carry `is_ap: True`; AP days remain in the lesson pool.
- With `gradingPeriods: {"9": "Q1"}`, week 9's row carries `grading_close: "Q1"`
  and no other week does.
- No sidecar file → behavior identical to today except exams now render from the
  calendar's own `nonClassDays`.

## Out of scope (possible follow-ups)

- **"Past/current/future" week shading** (bhs-cs's `when`), which needs "today"
  and isn't about exams.
