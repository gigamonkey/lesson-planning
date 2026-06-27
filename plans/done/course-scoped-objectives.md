# Course-scoped objectives

## Goal

Make each objective belong to exactly **one course**. Today objectives are
**shared** across courses (interned by text), so rewriting an objective's text
while working on course A silently changes it in course B too. In practice a
teacher editing a course is thinking about *that* course; a rewrite that makes
sense there shouldn't leak elsewhere.

Then add an explicit **"import objectives from another course"** action: it
copies a source course's objectives into the target, **re-interning them as new
objectives** (fresh uuids) owned by the target course.

## Current model (shared / interned by text)

- `objectives(uuid PK, text UNIQUE, status)` — one row per distinct text,
  globally. `schema.sql:77`.

- `course_objectives(course, uuid, position)` — the per-course pool (membership +
  order). `schema.sql:85`.

- `coverage(course, hierarchy, uuid, node_id, position)` — placements/coverage
  edges. `schema.sql:95`.

Interning is global: every create/import does find-or-create **by text**, so
identical text anywhere collapses to one `objectives` row with one uuid that
accumulates coverage edges across courses. Touch points:

- `import_objectives._resolve_upsert` (`import_objectives.py:82`) — the core
  find-or-create; `WHERE text=?` with no course.

- `objective_new` (`app.py:1463`), `objective_edit`'s clash check
  (`app.py:1499`), and `load_plan_text`'s token resolution (`app.py:1188`) — all
  intern / check uniqueness globally by text.

- `course_bundle.import_course` (`course_bundle.py:120`) — interns by text on
  import.

- `course_delete` (`app.py:906`) — must prune objectives left in **no** course
  (because they can be shared).

Note: the **on-disk corpus is already per-course** — each course dir has its own
`objectives.tsv` (`uuid<TAB>text`) and `coverage.tsv`. The sharing exists only in
the SQLite cache (and leaks back to disk because `write_course` writes whatever
uuid the cache interned to). So this change is mostly about the cache + the
import/intern paths; the file format is unchanged.

## Proposed model (course-scoped)

Each objective row belongs to one course. `uuid` stays the **global** primary key
(so `coverage`/foreign keys are untouched); two courses with the same text simply
get **different** uuids.

### Schema (`schema.sql`, bump `PRAGMA user_version`)

**DECISION (implemented): keep `course_objectives`, add `course` to
`objectives`.** The fold was rejected during implementation: `read_course` and
`load_plan_text` reset the pool (`DELETE course_objectives`) while deliberately
*keeping* `objectives`, because reference-hierarchy coverage points at those rows
and must survive a pool rebuild. Folding the pool into `objectives` would mean
deleting objective rows that coverage still needs, plus orphan-cleanup logic and
careful ordering — real risk for a cosmetic gain. So:

```sql
CREATE TABLE objectives (
  uuid   TEXT PRIMARY KEY,
  course TEXT NOT NULL REFERENCES courses(course),
  text   TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE (course, text)            -- interning key is now per-course
);
-- course_objectives unchanged: still the pool (membership + order).
```

`coverage` is unchanged (`uuid REFERENCES objectives(uuid)`). The mild redundancy
(`objectives.course` always equals `course_objectives.course`) is the price of
the simple, safe reset flow. `objectives.course` carries OWNERSHIP/identity (the
interning key); `course_objectives` carries pool membership + order.

Rejected alternative: composite key `(course, uuid)`. More FK churn across
`coverage`/tokens for no real benefit, since we re-mint uuids on collision anyway
(below).

### Interning becomes per-course

`_resolve_upsert` gains a `course` parameter and does find-or-create
`WHERE course=? AND text=?`. The important new rule: **if an incoming `uuid`
already belongs to a *different* course, mint a fresh uuid for this course** (and
remap this course's coverage/pool to it). This single rule does double duty:

- it's the **migration mechanic** (see below), and

- it's the **import-from-another-course** mechanic (re-intern as new).

### Code changes

- `import_objectives.py`: thread `course` through `_resolve_upsert`/`upsert`;
  per-course find-or-create; re-mint on cross-course uuid collision.

- `app.py`: `objective_new` (intern per course), `objective_edit` (clash check
  `WHERE course=? AND text=? AND uuid<>?`), `load_plan_text` token resolution
  (per course).

- `plan_io.py`: `read_course` / `load_plan_text` pass the course into interning.

- `course_delete` (`app.py:906`): simplify — just delete this course's
  objectives (no "orphaned in all courses" check needed once they're owned).

- `course_bundle.import_course`: objectives come in owned by the bundle's course;
  re-mint a uuid if it collides with an existing row.

- If folding `course_objectives` away: rewrite its JOINs to read `objectives`
  with a `course` filter — `workspace_data` (`app.py:959`), `render_course`
  (`plan_io.py:485`), the pool reorder in `place` (`app.py` pool branch), and the
  objectives-page / outline-context queries. (Grep `course_objectives`.)

- `write_course` / `objectives.tsv`: format unchanged; uuids are simply no longer
  shared across courses.

## Migration

The db is a disposable cache rebuilt from the corpus (`rebuild_db.py`), so there
is no in-place schema migration — bump `user_version` and rebuild. The real work
is splitting objectives that currently **share a uuid across courses** in the
committed corpus:

- On rebuild with per-course interning, the first course to load claims uuid `X`;
  a later course whose `objectives.tsv` also lists `X` hits the cross-course
  collision rule → it gets a fresh uuid `X'`, and its `coverage`/pool are remapped
  to `X'`. On the next `write_course`, that course's `objectives.tsv` is rewritten
  with `X'`. So the split is automatic on rebuild + re-export.

- Provide a one-shot: `rebuild_db.py` then write every course back
  (`plan_io.write_course` for each) so the corpus is normalized in one commit.
  Document that this re-mints duplicated uuids; it's a one-time, content-neutral
  churn of `objectives.tsv` (and any `coverage.tsv` referencing remapped uuids).

- Which course "keeps" the original uuid is arbitrary and irrelevant once they're
  independent.

## New feature: import objectives from another course

Backend: `copy_objectives(db, src_course, dst_course)` — read the source course's
pool objectives and intern each **text** into the destination (per-course
find-or-create → new uuids), appended to the destination pool; dedupe by text
within the destination. v1 copies **pool text only** (placements are
course-specific — the destination's outline/references differ, so coverage edges
don't transfer meaningfully). A later option could map coverage where reference
slugs match.

UI: a control on the destination course (objectives page or its setup block) —
"Import objectives from…" with a source-course picker.

Collab: this is a discrete bulk import, so commit it **immediately** via the
existing `commit_structural` path (see the collab autosave work) with a message
like `Import objectives from <src> into <dst>`. Add the endpoint to
`_IMMEDIATE_OPS`.

## Testing

- Two courses with identical objective text load as **distinct** objectives
  (different uuids); editing one leaves the other unchanged.

- Round-trip: write_course → read_course keeps objectives course-local; rebuild of
  a corpus with shared uuids splits them and re-export is stable thereafter.

- `course_delete` removes only its own objectives; another course keeps its copy.

- `copy_objectives` mints new uuids in the destination, dedupes by text, and (in
  collab) lands one commit.

- Extend `test_plan_io.py` / `test_schema_load.py` for the new schema and the
  per-course interning.
