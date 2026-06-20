# Unified hierarchies

Generalize the schema so **everything is a hierarchy of nodes**, and **objectives
are the connective tissue between nodes across hierarchies**. The CED, IB, and the
existing book are *reference* hierarchies; a course's lesson plan (and, later, a
planned book) are *outline* hierarchies. An objective's CED "coverage" and its
lesson "placement" become the same thing: a `coverage` edge from the objective to
a node in some hierarchy.

## Why

- A CED / IB / book tree isn't a "course" — it's a hierarchy. An objective can map
  into several at once (a CSA objective covering a CED leaf *and* an IB content
  node *and* a book section). `coverage` is already many-to-many; it just needs to
  be tagged by `hierarchy` instead of `course`.
- "Raw objective placed in lesson L (or roughly in unit U)" is the *same relation*
  as "objective covers CED node N" — deepest-level-wins placement is literally
  coverage at a node. So `units`/`lessons`/`plan_unit`/`plan_lesson` collapse into
  `nodes` + `coverage`, with one side table for a lesson's learning objective.
- An **outline of a different shape** (a planned book: chapters/sections, not
  units/lessons) then needs **no new tables** — it's just another hierarchy. That
  is the payoff that makes this worth the refactor.

## Schema

```sql
-- Registry of every hierarchy, reference or authored.
CREATE TABLE hierarchies (
  hierarchy TEXT PRIMARY KEY,   -- 'csa-ced', 'csp-ced', 'ib', 'bhsawesome-book', 'csa-plan', 'new-book-draft'
  kind      TEXT NOT NULL,      -- 'reference' (regenerated from markdown) | 'outline' (authored in-app)
  title     TEXT NOT NULL,      -- human label
  source    TEXT                -- reference: the markdown path; outline: NULL
);

-- Nodes of any hierarchy. Reference rows are regenerated from markdown (read-only,
-- node_id = verbatim id). Outline rows are authored in-app (node_id = uuid;
-- parent_id / ordinal mutable via reorder/reparent).
CREATE TABLE nodes (
  hierarchy TEXT NOT NULL REFERENCES hierarchies(hierarchy),
  node_id   TEXT NOT NULL,
  parent_id TEXT,
  level     TEXT NOT NULL,      -- per-hierarchy vocab: unit/topic/lo/ek … or unit/lesson … or chapter/section
  ordinal   INTEGER NOT NULL,
  is_leaf   INTEGER NOT NULL,
  text      TEXT NOT NULL,      -- title / statement
  PRIMARY KEY (hierarchy, node_id)
);

-- Objective <-> node, in ANY hierarchy. Reference edges = "covers this standard";
-- outline edges = "placed at this lesson/unit". Multiple edges per (hierarchy,uuid)
-- are allowed by the schema; the app may enforce single-placement per outline (see
-- "Cardinality").
CREATE TABLE coverage (
  hierarchy TEXT NOT NULL,
  uuid      TEXT NOT NULL REFERENCES objectives(uuid),
  node_id   TEXT NOT NULL,
  PRIMARY KEY (hierarchy, uuid, node_id),
  FOREIGN KEY (hierarchy, node_id) REFERENCES nodes(hierarchy, node_id)
);

-- Generic per-node extras for authored outlines (sparse, stringly-typed).
-- e.g. ('csa-plan', <lesson-uuid>, 'learning_objective', 'Declare and use variables').
CREATE TABLE node_attr (
  hierarchy TEXT NOT NULL,
  node_id   TEXT NOT NULL,
  name      TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (hierarchy, node_id, name),
  FOREIGN KEY (hierarchy, node_id) REFERENCES nodes(hierarchy, node_id)
);

-- Pair an outline with the reference(s) it is measured against, so the UI can show
-- coverage stats (gaps/planned) of the outline against the reference.
CREATE TABLE hierarchy_targets (
  outline   TEXT NOT NULL REFERENCES hierarchies(hierarchy),
  reference TEXT NOT NULL REFERENCES hierarchies(hierarchy),
  PRIMARY KEY (outline, reference)
);

-- Unchanged.
CREATE TABLE objectives (uuid TEXT PRIMARY KEY, text TEXT NOT NULL,
                         status TEXT NOT NULL DEFAULT 'active');

-- The raw-objective pool, still course-scoped (membership + pool order). The plan
-- placement columns are gone -- placement is a coverage edge now.
CREATE TABLE course_objectives (
  course   TEXT NOT NULL,
  uuid     TEXT NOT NULL REFERENCES objectives(uuid),
  position INTEGER,
  PRIMARY KEY (course, uuid)
);
```

**Removed:** `units`, `lessons` (become outline nodes + `node_attr`);
`course_objectives.plan_unit` / `plan_lesson` (become `coverage` edges into the
outline hierarchy).

## Semantics

**Stats / worklist** are computed for an outline `O` against its target reference
`R` (from `hierarchy_targets`). A reference leaf `n` in `R` is:

- **planned** — an active objective covers `n` in `R` *and* is placed at a **leaf**
  of `O` (a lesson),
- **rough** — covered, and the objective is placed at a **non-leaf** `O` node (a
  unit) but no leaf,
- **objective-only** — covered, but the objective is placed nowhere in `O`,
- **gap** — no active objective covers `n` in `R`.

```sql
-- "planned" leaves of reference R via outline O:
SELECT n.node_id FROM nodes n
WHERE n.hierarchy = :R AND n.is_leaf = 1
  AND EXISTS (
    SELECT 1
      FROM coverage cr
      JOIN objectives ob ON ob.uuid = cr.uuid AND ob.status = 'active'
      JOIN coverage co   ON co.uuid = cr.uuid AND co.hierarchy = :O
      JOIN nodes   onode ON onode.hierarchy = :O AND onode.node_id = co.node_id
                        AND onode.is_leaf = 1
     WHERE cr.hierarchy = :R AND cr.node_id = n.node_id);
```

This is exactly today's worklist, parameterized by `(O, R)` instead of hardcoding
`'csa'` for both.

**The two UI surfaces become the same shape — a hierarchy tree with objectives at
its nodes** — differing only in editability:

- *Reference view* (today's Outline page): render `R`; each leaf shows the
  objectives that cover it (`coverage(R, …)`); add/edit objectives and edit
  coverage edges. Tree structure is fixed (from markdown).
- *Outline editor* (today's Plan page): render `O`; edit the tree itself (node
  CRUD — add/rename/reorder/reparent/delete), drag raw objectives onto nodes
  (insert `coverage(O, …)`), edit a lesson's learning objective
  (`node_attr(O, lesson, 'learning_objective', …)`). The raw pool = course
  objectives not yet placed in `O`.

**`render_outline`** generalizes to: render outline `O` (its tree, the objectives
placed at each node, each objective annotated with its `R` coverage), plus the
traceability appendix (each `R` leaf → the `O` lessons that cover it through shared
objectives) and the gap list.

## Cardinality

The schema allows an objective to have many `coverage` edges in one hierarchy. For
**reference** hierarchies that's expected (covers several leaves). For **outline**
hierarchies the app currently enforces a single placement (delete-then-insert on
drag); per the design decision, this is an *app* rule we can relax later (place a
raw in multiple lessons) with **no migration**.

## Export / rebuild

`coverage` is fully authored data (the curated objective↔node map *and* the
placement), so it's exported in full. `nodes` is now split by `hierarchies.kind`:

- **reference** nodes: regenerated from markdown (`load_nodes`), **not** exported.
- **outline** nodes: authored → **exported** (along with `node_attr`).

So the export set becomes: `objectives`, `course_objectives`, `coverage`,
`hierarchies`, `hierarchy_targets`, `node_attr`, and **`nodes` filtered to outline
hierarchies**. `rebuild_db` then: apply `schema.sql` → `load_nodes` for each
reference hierarchy → `import_planning` for everything authored (including outline
nodes).

## Migration (current → unified)

Plan data is tiny today, so this is cheap data-wise; the code is the work.

1. Rename `nodes.course` → `hierarchy`, `coverage.course` → `hierarchy`. Rename the
   existing `'csa'` hierarchy id to `'csa-ced'` (it holds the CED). *(This is the
   standalone "cosmetic" first step.)*
2. Create `hierarchies`; register `('csa-ced','reference',…, 'csa/ced-2025-hierarchy.md')`
   (and csp/ib/book when present).
3. Create outline hierarchy `('csa-plan','outline','CSA Lesson Plan', NULL)`. Convert
   `units` → nodes (`level='unit'`, parent NULL, ordinal=position) and `lessons` →
   nodes (`level='lesson'`, parent=unit_id, ordinal=position); copy each lesson's
   `learning_objective` to `node_attr(csa-plan, lesson, 'learning_objective', …)`.
4. Convert placement to coverage: `plan_lesson`/`plan_unit` → `coverage('csa-plan', uuid, node)`.
5. `hierarchy_targets('csa-plan','csa-ced')`.
6. Drop `units`, `lessons`, and `course_objectives.plan_unit/plan_lesson`.

## Naming notes

- `coverage` now means both "covers a standard" and "placed at a node." The name
  still reads ("objective covers node"); keep it for continuity, or rename to
  something neutral (`placements` / `node_objectives`) — cosmetic, decide later.
- `course_objectives` stays course-scoped (the pool). Pool `position` is per-course;
  if one course ever has two outlines wanting different pool orders, revisit
  (per-outline order) — not now.

## Staged implementation

- **Stage 0 — rename + registry.** `course → hierarchy` on `nodes`/`coverage`; add
  `hierarchies` and seed it. Standalone, low risk, ships the multi-hierarchy
  mapping ability immediately. (Migration step 1–2.)
- **Stage 1 — unify the lesson plan.** Convert `units`/`lessons`/placement to the
  outline model (steps 3–6); add `node_attr`, `hierarchy_targets`; rewrite the Plan
  page as an outline editor, `render_outline`, and export/rebuild. The big change.
- **Stage 2 — second outline shape.** Stand up a "planned book" outline to validate
  generality (different `level` vocab, same tables); ideally a generic
  outline-editor component shared with the lesson plan.

## Open / deferred

- A generic outline-editor UI shared across outline kinds (lesson plan, planned
  book) — likely falls out of Stage 1 but confirm in Stage 2.
- Whether reference and outline tree views should literally be one component.
- Per-outline (vs per-course) pool ordering, if/when needed.
