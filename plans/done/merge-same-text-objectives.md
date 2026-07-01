# Merging same-text objectives across branches

## The problem

Identity of an objective is its `uuid`, but the model's natural key is
`(course, text)` — `UNIQUE(course, text)` in `schema.sql`. An objective is
persisted in three places (`FORMAT.md`, `plan_io.py`):

- `objectives.tsv` — `(uuid, text)`, sorted by uuid
- `plan.md` — a bullet `- <text>  (#<uuid-prefix>)` whose position under a
  lesson heading *is* the placement
- `coverage.tsv` — `(uuid, hierarchy_id, node_id)` reference coverage edges

When two users on separate branches each create an objective with the **same
text**, they mint **different uuids**. After a clean git merge (ignoring
line-level conflicts) the merged tree holds two `objectives.tsv` rows with the
same text and two uuids, two bullets in `plan.md`, and possibly two sets of
reference edges. This violates the model's own `UNIQUE(course, text)` invariant —
a state only a merge can produce.

## What happens today (silent corruption, not a merge)

Tracing `read_course` on the merged tree:

1. The objectives-insert loop uses `INSERT OR IGNORE`. The first uuid (smallest,
   since the TSV is uuid-sorted) wins; the second hits `UNIQUE(course, text)` and
   is **silently dropped**.
2. `_resolve_bullets` resolves the loser's token uniquely, then
   `UPDATE objectives SET text=… WHERE uuid=<loser>` matches **zero rows** (the
   loser was never inserted). The subsequent `INSERT OR IGNORE` into
   `course_objectives` and `coverage` for the loser **succeed**, because foreign
   keys are off — so the loser becomes an orphan referenced by pool + placement
   rows but absent from `objectives`.
3. `render_course` builds the pool with an **inner** join
   `course_objectives ⋈ objectives`, so the loser drops out — but its outline
   coverage row survives and is still emitted as a placement with **empty text**
   (`-   (#<loser>)`), and `objectives.tsv` loses the row entirely. On the next
   round-trip that empty bullet no longer resolves and mints a fresh empty
   objective — the corruption compounds.

## What should happen

Unify all same-text objectives in a course under one winning uuid, and rewrite
every reference (outline placements, reference coverage edges, pool membership)
to the winner. This is the only resolution consistent with `UNIQUE(course, text)`:
two same-text rows are definitionally the same objective minted twice, and the
model cannot keep them distinct anyway.

One part is **not** auto-resolvable: the outline is single-placement per
objective, but the two users may have placed their bullets in different lessons.
After unifying identity, the one objective now carries two outline coverage edges.
No rule knows which lesson is correct — that is the human part of the conflict.

## Plan

### (1) Load-time unification — `read_course`

`read_course` already has the right mechanism: the `remap` dict it uses to
re-mint uuids that collide across courses. Extend it into a single canonical map
`canon: disk-uuid -> uuid-this-course-uses` that handles both cases:

- Group `objectives.tsv` rows by `text`; the first row per text (smallest uuid)
  is the winner. Later same-text rows map to the winner (and are not inserted).
- A uuid already owned by a **different** course is re-minted, as today.
- Insert only winners into `objectives`.
- Apply `canon` everywhere a disk uuid is used:
  - token resolution in `_resolve_bullets` (resolve the token against the full
    disk uuid list so the loser's token still resolves, then map through `canon`),
  - the `coverage.tsv` loop,
  - pool membership (`course_objectives`).
- `INSERT OR IGNORE` already dedups the resulting collisions.

This auto-heals on first load; because the app autosaves, the next write produces
a clean canonical corpus.

### (2) Surface the placement conflict — don't resolve it silently

After unification, detect objectives with more than one outline placement. **Keep
both edges** (lossless — the objective shows up under both lessons in the outline,
which is itself the surfacing) and emit a clear warning from `read_course` (same
`print` channel as the existing slug warnings) naming the objective and the
lessons. Also warn for each set of unified duplicates.

### (3) Turn on `PRAGMA foreign_keys = ON`

Foreign keys are off (SQLite default), which is what let the orphan rows survive
silently. Enable enforcement at every connection. This requires reordering the
one load path that currently inserts a parent-pointer before its target:
`read_course` upserts `courses.primary_outline` before the outline hierarchy
exists, and deletes a course's `hierarchies` while `courses.primary_outline`
still points at one. Fix by nulling `primary_outline` before the delete, inserting
`courses` with a null pointer, and setting it at the end once the outline
hierarchy exists.

## Tests

Add a round-trip check to `test_plan_io.py`: build a corpus directory with two
`objectives.tsv` rows of identical text (two uuids), two bullets placed in
different lessons, and a reference edge on each uuid; assert that after
`read_course` there is exactly one objective for that text, the pool count is
right, both placements survive on the surviving uuid, and the reference edges are
unified onto it.
