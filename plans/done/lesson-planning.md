# Lesson Planning System

A local web app for turning hand-written learning objectives + the official
course hierarchies into a **traceable year-long lesson-plan outline** for each
course (CSA first, then CSP and IB).

## Goal

For each course produce an outline of the year's lessons that may be organized
**differently** from the official course outline, while preserving
**traceability**: every node of the official outline (CED essential knowledge,
IB content statement) is provably covered by some objective that is scheduled in
some lesson — and nothing is silently dropped.

Four jobs the system must do (the user's requirements):

1. **Map** hand-written objectives into the existing hierarchies.
2. **Identify gaps** — official nodes covered by no objective.
3. **Collapse duplicates** — not a separate dedup step. Distilling raw objectives
   into lesson objectives (Phase 3) naturally merges overlaps, so an explicit
   dedup pass is redundant and was dropped.
4. **Organize** objectives into an ordered lesson plan (its own structure, not
   the CED's) while keeping the map back to the official outline.

## Decisions (settled)

- **Form factor:** local interactive web app.
- **Scope:** build CSA end-to-end first; keep schema course-agnostic so CSP/IB
  slot in once their objectives exist.
- **Lesson model:** sequence-only for v1 — lessons are an ordered list with a
  title. Time budget / resource links / assessment mapping are deferred (hooks
  left in the schema, not built).
- **Two levels of teacher content:** *raw objectives* (the ~361 drafted atoms, to
  mapped to the CED) and *lesson objectives* (the student-facing
  statement written on the whiteboard, synthesized from one or more raw
  objectives). End state: **one lesson objective per lesson.**
- **Mapping granularity:** an objective may map to any node level, but only
  **leaf** coverage counts toward the "everything is covered" guarantee.
- **Stack:** Flask + SQLite + htmx + SortableJS (confirmed).

## What already exists (reuse, don't rebuild)

- **Hierarchies** as markdown → `build_hierarchy_db.py` → wide SQLite tables
  (CSA `unit/topic/lo/ek`, CSP `bi/eu/lo/ek`, IB `theme/topic/subtopic/ls/content`).
- **CSA objectives**: 361 hand-written, OCR'd in `csa/learning-objectives/csa.txt`,
  labeled with best-match CED coordinates in `objectives.tsv`, loaded by
  `load_objectives.py` into normalized tables (`objectives`, `course_objectives`,
  `csa_objectives`).
- **Similarity utilities**: `jaccard.py`, `lcs.py` (used by `compare_activities.py`;
  tried for dedup and dropped — see the dedup section).
- **Seed db**: `lesson-planning/db.db` (currently just `csa_ced`) and a
  `lesson-planning/schema.sql` sketch — this plan supersedes that sketch.

Current state, measured (CSA): 223 EK leaves, **184 covered**, **39 gaps**;
several EKs have 6–8 objectives mapped (e.g. `4.5.A.1`, `2.9.A.1`) — overlapping
objectives that get distilled into a single lesson objective in Phase 3. So the
app has real work to show on day one.

## Source-of-truth model (resolves the file-vs-DB tension)

The repo's house style is "files in git are source of truth, DB is derived." An
interactive app needs to write to the DB. Resolution:

- **Hierarchies stay file-sourced.** Regenerated from the `*-hierarchy.md` files;
  the app treats them as read-only reference. Re-import on change.
- **The lesson-planning DB is the live store for *planning data*** — objective
  edits, coverage mappings, lessons.
- **Round-trip to git-diffable files.** An `export` step dumps objectives,
  coverage, and lessons back to TSV/markdown so the canonical state is still
  reviewable in a PR and reproducible. `import` seeds the DB from those files.
  The DB is a working copy; the files are the committed snapshot.

## Data model

One normalized SQLite db (`lesson-planning/db.db`), course-agnostic. Replaces the
per-flavor wide tables *inside the app* with a uniform node table so gap/coverage
queries are one query regardless of course flavor.

```sql
-- Official outline nodes, normalized across all flavors. Derived from
-- build_hierarchy_db.py output by a new load_nodes.py adapter.
CREATE TABLE nodes (
  course    TEXT NOT NULL,      -- 'csa' | 'csp' | 'ib'
  node_id   TEXT NOT NULL,      -- verbatim id, e.g. '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'
  parent_id TEXT,               -- deepest non-null ancestor; NULL for level-1
  level     TEXT NOT NULL,      -- level tag: 'unit'|'topic'|'lo'|'ek'|...
  is_leaf   INTEGER NOT NULL,   -- 1 if no children (the unit of "coverage")
  ordinal   INTEGER NOT NULL,   -- document order, for stable display
  text      TEXT NOT NULL,
  PRIMARY KEY (course, node_id)
);

-- RAW objectives: the atoms the teacher drafted, mapped to the CED.
-- Course-agnostic text; a raw objective can belong to >1 course.
CREATE TABLE objectives (
  uuid        TEXT PRIMARY KEY,
  text        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'merged' | 'draft'
  merged_into TEXT REFERENCES objectives(uuid) -- reserved; explicit merge dropped
);

CREATE TABLE course_objectives (
  course TEXT NOT NULL,
  uuid   TEXT NOT NULL REFERENCES objectives(uuid),
  PRIMARY KEY (course, uuid)
);

-- Many-to-many: a raw objective covers >=1 official node; a node may be covered
-- by >=1 raw objective. Replaces csa_objectives' single-node-per-row encoding.
CREATE TABLE coverage (
  course  TEXT NOT NULL,
  uuid    TEXT NOT NULL REFERENCES objectives(uuid),
  node_id TEXT NOT NULL,
  PRIMARY KEY (course, uuid, node_id),
  FOREIGN KEY (course, node_id) REFERENCES nodes(course, node_id)
);

-- The teacher's own structure: an ordered list of lessons per course.
CREATE TABLE lessons (
  id       INTEGER PRIMARY KEY,
  course   TEXT NOT NULL,
  title    TEXT NOT NULL,
  position INTEGER NOT NULL          -- order within the course
);

-- LESSON objectives: the student-facing statement (whiteboard objective),
-- synthesized from >=1 raw objective. End state: one per lesson. lesson_id is
-- nullable while a lesson objective is still being drafted/unscheduled.
CREATE TABLE lesson_objectives (
  id        INTEGER PRIMARY KEY,
  course    TEXT NOT NULL,
  text      TEXT NOT NULL,
  lesson_id INTEGER REFERENCES lessons(id),
  position  INTEGER                  -- order within the lesson (NULL = unscheduled)
);

-- Roll-up: which raw objectives a lesson objective encompasses (many-to-many).
-- This is the hop that lets several raw objectives collapse into one whiteboard
-- statement while preserving the trace down to each raw objective's CED nodes.
CREATE TABLE objective_rollup (
  lesson_objective_id INTEGER NOT NULL REFERENCES lesson_objectives(id),
  objective_uuid      TEXT    NOT NULL REFERENCES objectives(uuid),
  PRIMARY KEY (lesson_objective_id, objective_uuid)
);
```

Deferred-but-anticipated (not built in v1): `lessons.est_days`,
`lesson_resources(lesson_id, kind, ref)` for book sections / activities / decks,
`assessment_coverage(uuid, source, item_id)`.

### Coverage in three stages (this is the heart of traceability)

The two-level content model gives three progressive worklists, each a query and
each a screen filter:

1. **CED gap** — a leaf node with no `active` raw objective in `coverage`. (39
   today.) *Fix:* write a raw objective.
2. **Unsynthesized** — an `active` raw objective in no `objective_rollup`, i.e.
   not yet folded into a lesson objective. *Fix:* synthesize it into one.
3. **Unscheduled** — a lesson objective with `lesson_id IS NULL`. *Fix:* place it
   in a lesson.

The headline guarantee — what the user means by "covering everything somewhere" —
is **plan coverage of every leaf node**: a leaf is plan-covered when a chain
exists `node ← coverage ← active raw objective ← rollup ← lesson objective ←
scheduled lesson`. Anything short of that chain lands in one of the three lists.

```sql
-- Leaf nodes NOT plan-covered: no scheduled lesson traces back to them.
SELECT n.course, n.node_id, n.text
FROM nodes n
WHERE n.is_leaf = 1
  AND NOT EXISTS (
    SELECT 1
    FROM coverage c
    JOIN objectives o         ON o.uuid = c.uuid AND o.status = 'active'
    JOIN objective_rollup r   ON r.objective_uuid = c.uuid
    JOIN lesson_objectives lo ON lo.id = r.lesson_objective_id
    WHERE c.course = n.course AND c.node_id = n.node_id
      AND lo.lesson_id IS NOT NULL);
```

## Pipeline (scripts, house style)

```
*-hierarchy.md ──build_hierarchy_db.py──► wide table ──load_nodes.py──► nodes
objectives.tsv ──────────────────────────────────────import_objectives──► objectives + course_objectives + coverage
                                                                              │
                                                  (interactive edits in app) ─┤
                                                                              ▼
                                          export ──► objectives.tsv + coverage.tsv + lessons.md (git snapshot)
                                                                              │
                                                            render_outline ──► csa/lesson-plan.md (the deliverable)
```

New scripts:

- `load_nodes.py` — normalize a `build_hierarchy_db.py` wide table into `nodes`
  (compute `parent_id`, `level`, `is_leaf`, `ordinal`). Course-agnostic; the one
  adapter that hides flavor differences from the app.
- `import_objectives.py` — seed `objectives`/`course_objectives`/`coverage` from
  `objectives.tsv` (each non-`none` row → one `coverage` edge to its `ek`).
  Supersedes the single-table `load_objectives.py` mapping.
- `export_planning.py` — dump live DB state back to TSV/markdown for git.
- `render_outline.py` — emit the year outline + traceability appendix as
  markdown (and reuse the existing XML/HTML rendering pattern if a printable
  version is wanted later).

## App architecture

Stack: **Flask + SQLite + htmx + SortableJS**, server-rendered. Rationale: Python
+ `uv` is the house language; htmx keeps interactivity without a JS build step;
SortableJS gives drag-and-drop reordering for the one screen that needs it.
(Alternative if you'd rather: FastAPI, or a static SPA — but Flask+htmx is the
lowest-friction fit for this repo. Flag if you disagree before Phase 1.)

Screens:

1. **Hierarchy + coverage** (job 1 & 2) — the CED/syllabus tree, each leaf
   annotated with its mapped objectives and a status badge: *gap* /
   *objective-only* / *planned*. Filter to "gaps only" → the gaps worklist. Each
   leaf's objectives sit in a bordered box as an editable bulleted list, with a
   "+ add objective" that creates a new objective (new uuid) mapped to that node
   — so gaps can be authored in place, right where they show up.
2. **Objectives + mapping** (job 1) — objective list; **edit** text in place
   (same uuid), map/unmap coverage nodes (validated, datalist picker), and add
   new objectives. No dedup step — duplicates collapse during synthesis (job 3).
3. **Lesson builder** (job 4) — two moves. (a) *Synthesize*: select one or more
   raw objectives and roll them into a **lesson objective** (the whiteboard
   statement), drafting its text. (b) *Schedule*: drag lesson objectives from the
   unscheduled pool into ordered lessons (end state: one per lesson), reorder
   within/across. Live counters for the three worklists (CED gaps / unsynthesized
   raw / unscheduled lesson objectives) and planned-leaf-coverage %.
4. **Coverage report** (traceability) — every leaf → the raw objective(s) → the
   lesson objective → the lesson that covers it; export button → `lesson-plan.md`.

## Dedup: dropped (subsumed by synthesis)

There is no explicit dedup step. Two approaches were tried and removed:

- **Text similarity (jaccard/lcs)** — too noisy. So many objectives share the
  same "Write code to …" phrasing that similarity is mostly false positives.
- **Grouping by shared coverage node** — structurally clean (objectives on the
  same EK), but still a manual pass that turned out not to be worth it.

The reason it doesn't matter: raw objectives get **distilled into lesson
objectives** in Phase 3, and that synthesis naturally collapses overlapping or
duplicate raw objectives into one whiteboard statement. Job 3 ("remove
duplicates") is therefore satisfied as a side effect of job 4, not by a separate
screen.

The schema keeps the `status` / `merged_into` columns (harmless, and a future
explicit merge could reuse them), but nothing writes `merged` today.

## Other LLM assists (human always approves)

- **Mapping new objectives → nodes**: suggest best-match nodes (LLM pass over
  node text, automatable via `claude -p`/API). The original `objectives.tsv`
  mapping was exactly this, done once in bulk; now incremental and in-app.
- **Gap filling**: for an uncovered node, optionally suggest objective text the
  teacher can accept/edit into a new objective.

These are convenience layers over a fully manual core — the app is usable with
zero LLM calls; automation is an optimization, not a dependency.

## Build order

- **Phase 0 — schema + seed (no UI).** Finalize schema above; write
  `load_nodes.py`, `import_objectives.py`, `export_planning.py`. Seed
  `lesson-planning/db.db` from CSA. Verify the gap query reproduces 39 gaps.
- **Phase 1 — read-only app.** Flask app, screens 1 & 4 (hierarchy+coverage,
  report). Immediate value: visualize gaps and current mapping. Validates the
  data model before any write paths.
- **Phase 2 — objectives + mapping (writes).** Screen 2: edit objective text,
  map/unmap coverage nodes, add objectives; export round-trip to TSV. (Dedup was
  prototyped here and dropped — see the Dedup section.)
- **Phase 3 — lesson builder.** Screen 3: synthesize raw → lesson objectives,
  then lessons CRUD + drag/drop scheduling + live coverage; `render_outline.py`
  produces `csa/lesson-plan.md` — **the deliverable**.
- **Phase 4 — generalize.** Author CSP and IB objectives; everything else
  already course-agnostic. Optionally add deferred lesson fields (hours/dates via
  the existing `ib/ib-hours.tsv`, resource links to the book hierarchy).

## The deliverable

`<course>/lesson-plan.md`: ordered lessons, each with its **one lesson objective**
and the raw objectives it rolls up, plus a **traceability appendix** mapping every
official node → raw objective → lesson objective → lesson, plus a **gap list** of
anything still uncovered. Git-committed, regenerable.

## Resolved decisions

- **Granularity:** objectives may map to any node level; only leaf coverage counts
  toward the guarantee. (Q1 — settled.)
- **Two content levels + one objective per lesson:** raw objectives roll up into
  lesson objectives via `objective_rollup`; a lesson ends with one lesson
  objective. (Q2 — settled, and the reason for the extra hop in the schema.)
- **Stack:** Flask + htmx + SortableJS. (Q3 — settled.)
```
