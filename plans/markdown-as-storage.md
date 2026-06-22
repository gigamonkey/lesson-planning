# Plan: Markdown as the fundamental non-database storage format

## Goal

Move the curriculum-hierarchy **markdown parser** into this repo and make
**markdown the canonical, human-editable on-disk form of a course**. The SQLite
database stops being a thing we snapshot and becomes a pure cache: it is *loaded
from* a directory of markdown files (plus two small normalized TSVs) and
*exported back to* those same files. The files live in git, are edited by hand,
and are the source of truth.

Two structural consequences:

- **The JSON intermediary goes away.** Today a hierarchy is authored as markdown
  in `hierarchy-extractors`, converted to node-list JSON by
  `build_hierarchy_json.py`, and ingested here by a "dumb loader" (`load_nodes.py`)
  that does no markdown parsing. After this change, *this* repo parses the
  markdown directly. The cross-repo contract changes from "node-list JSON" to
  "conforming markdown files."

- **The wide `export/*.tsv` DB-dump goes away.** Today `export_planning.py` dumps
  seven planning tables to TSV and `import_planning.py` reloads them — a near-1:1
  mirror of the database. After this change the hierarchies live in markdown and
  the only relational state that stays tabular is the connective tissue that is
  *inherently* many-to-many across hierarchies: objective identity and coverage.

The split between the two repos moves to where it belongs: **this repo defines
the markdown format it needs**, and `hierarchy-extractors` becomes exactly what
its name says — tools that turn an out-of-our-control source (a CED PDF, the IB
syllabus PDF, a PreTeXt book, …) into *conforming* markdown.

## Why this is the right cut

The earlier split (see `plans/done/extract-extractors.md`) put the format's
*definition* in the producer (`hierarchy-extractors`) and shipped the parsed
result here as JSON so this repo could stay a dumb loader. But this repo is the
one that actually *edits* hierarchies (it authors lesson-plan outlines in-app and
needs to write them back out), and "the human-editable form of a course" is a
product concern that belongs to the app, not to a PDF scraper. Owning the format
here lets the same code both **read** an authored markdown course and **write**
one back — which the JSON-in-one-direction design could never do.

## What does NOT change

**The database schema is untouched.** `schema.sql` and all eight tables
(`courses`, `hierarchies`, `nodes`, `objectives`, `course_objectives`,
`coverage`, `node_attr`, `hierarchy_targets`) stay exactly as they are. This is
an **I/O-layer rewrite**, not a data-model change — everything below is about how
those tables are populated from disk and serialized back to it. The app's
in-memory/DB behavior, the workspace, the tree views, gap/coverage queries are
all unaffected.

## The end-state file model

A **corpus** is a directory of **course directories**. One course directory holds
everything that describes one course:

```
courses/                     # the corpus root (replaces export/ AND the seed dir)
  csa/
    plan.md                  # the OUTLINE (course flavor) + course-level wiring in front matter
    csa-ced.md               # a reference hierarchy (csa flavor)   — read-only input
    csa-book.md              # another reference (book flavor)      — read-only input
    objectives.tsv           # (uuid, text)            — machine-maintained, git-diffable
    coverage.tsv             # (uuid, hierarchy_id, node_id) — machine-maintained
  widgets/
    plan.md
    widgets-ced.md
    objectives.tsv
    coverage.tsv
```

Three file roles:

- **Reference hierarchy markdown** (`csa-ced.md`, `widgets-ced.md`, …) — one file
  per external standard, in its flavor's heading shape (`csa`/`csp`/`ib`/`book`).
  These are **inputs** the extractor (or a human) produces; the app loads them and
  **never rewrites them**. They map to `hierarchies.editable = 0`.

- **The outline markdown** (`plan.md`) — the course's authored lesson plan, in the
  `course` flavor (`# Unit N:` → `## N.1 Lesson` → `- objective` bullets). This is
  the one hierarchy file the app **writes**. It maps to `hierarchies.editable = 1`.
  Its front matter additionally carries the **course-level wiring** that lives in
  no single hierarchy (see below).

- **The two TSVs** — the normalized connective tissue. Machine-maintained but
  git-diffable, dumped into the course directory on export, read on load.

**The corpus root is both the load source and the export target.** Today those
are two different things — a `LESSON_SEED_DIR` manifest you load from and an
`export/` directory you snapshot to. They collapse into one round-tripping
directory, which is the literal realization of "load the DB from markdown, export
back to the same markdown."

### Slugs

Each hierarchy's `hierarchy_id` (its slug — the key used in `coverage.tsv`,
`hierarchy_targets`, and the `nodes.hierarchy` column) is the **filename stem**:
`csa-ced.md` → `csa-ced`, `plan.md` → `plan`. A front-matter `slug:` overrides it
when the id must differ from the filename.

## The format

### Reference profile (unchanged from what extractors emit)

A reference file is the existing numbered-markdown hierarchy: an optional `---`
front-matter block (`title:`, `kind:`, optional `slug:`) followed by ATX headings
whose depth encodes tree depth, each heading carrying `ID␠TEXT`. The level-1
heading both names the root and signals the flavor (`# Unit N:`,
`# Big Idea N: … (CODE)`, `# Theme X:`, `# Chapter N:`). This is precisely what
`hierarchy.py` already parses — see the table in `hierarchy-extractors/HIERARCHIES.md`.

Loading one is mechanically the same as today, minus the JSON hop: parse markdown
→ flat already-tagged node list → `nodes` rows.

### Plan profile (the outline — new authoring/serialization here)

`plan.md` is the `course` flavor extended in three ways. Example:

```markdown
---
course: csa
title: AP Computer Science A
primary_reference: csa-ced
primary_outline: plan
targets:
  - csa-ced
  - csa-book
---

# Unit 1: Widget Basics

## 1.1 What Is a Widget

**Learning objective:** Describe a widget and name its parts.

- Name the two main parts of a widget.  (#a3f2)
- Explain what the frobnicator does.  (#1b9e)

## 1.2 Assembling Widgets

- List the assembly steps for a widget in order.  (#7c04)

# Unit 2: Advanced Widgetry

## 2.1 Widget Maintenance

- Troubleshoot a frobnicator that will not spin.  (#d550)

## Pool — not yet placed

- Brainstorm a class project that uses widgets.  (#e2a1)
```

The three extensions over the plain `course` flavor:

1. **Front-matter wiring.** The presence of a `course:` key marks this file as the
   course descriptor/outline. It carries `course` (id), `title`, the
   `primary_reference` / `primary_outline` slugs, and `targets` (the outline →
   reference pairs for `hierarchy_targets`). This is the home the user chose for
   facts that belong to no single hierarchy.

2. **Per-lesson learning objective.** A `**Learning objective:** …` line directly
   under a lesson heading becomes a `node_attr(hierarchy=plan, node_id=<lesson>,
   name='learning_objective', value=…)` row. (Today `node_attr` is used *only* for
   this, so it is fully captured structurally and needs no TSV.)

3. **Objective bullets are placements, not nodes.** This is the key semantic
   difference from the generic `course` parser. In the existing `hierarchy.py`, a
   column-0 bullet under a lesson becomes a synthesized **level-3 node**. In the
   plan profile, a bullet is instead a **raw objective placed in the enclosing
   lesson** — interned by its text into `objectives` and given a `coverage` edge
   to the lesson node. The outline hierarchy's own nodes are therefore only
   **units and lessons**; objectives ride on top via coverage, exactly as the
   live schema already models them.

   A `## Pool — not yet placed` section (a lesson-less bullet list) holds raw
   objectives that are in the course pool (`course_objectives`) but not placed in
   any lesson — the markdown home for the app's existing "rough/unplaced" notion.
   Document order of all bullets (placed + pooled) is the pool `position`.

Objective identity rides on a short **token**, not on the bullet text — see
*Objective identity* below. The token is the only id-like thing in the file; full
uuids never appear.

### Outline node ids are positional

Unit/lesson node ids are derived positionally from the markdown (`1`, `1.1`,
`1.2`, `2`, …), the same way the `course` flavor already synthesizes them — *not*
the in-app uuids the outline currently stores in `nodes.node_id`. Because
placement (objective → lesson) and the learning-objective attr are both encoded
**structurally** (a bullet/line sitting under a heading), they travel with the
lesson across an export → reload round-trip even though the positional id is
regenerated each load. Reordering or renaming lessons in the app simply produces
different document order, hence different ids, on the next export — and the
structural placements move with them. This removes the need to persist outline
uuids on disk at all.

### Objective identity — abbreviated uuid tokens

Each objective bullet ends with a short **identity token** in parens: the shortest
prefix of the objective's uuid that is unique among the course's objectives, with
a `#` sigil to set it apart from any literal parens in the objective text:

```
- Explain what the frobnicator does.  (#1b9e)
```

On **export**, walk the course's whole objective set, compute each uuid's shortest
unique prefix (with a small floor — say 4 hex chars — to limit diff churn and
accidental collisions), and append `(#<prefix>)` to every bullet, placed and
pooled alike.

On **load**, resolve each bullet's trailing `(#…)` token to a full uuid by
prefix-matching against the uuids in `objectives.tsv`:

- **Token resolves to exactly one uuid** → that is the objective. Adopt the
  bullet's *text* as the objective's text (markdown is the human source of truth),
  so a reword propagates while identity — and therefore coverage — is preserved.

- **No token, or a token matching zero or more than one uuid** → fall back to
  interning by exact text (reuse an existing objective with that text, else mint a
  fresh uuid). This is how a hand-typed new bullet — or a deliberately cleared
  token — becomes a new objective.

This is the mechanism the user proposed, and it **dissolves the "editing text
orphans coverage" problem**: identity now rides on the token, not the text, so an
objective can be reworded in place. The token is recognized only as the *trailing*
parenthesized group matching `#[0-9a-f]+`, so ordinary parens inside objective text
are never mistaken for it.

### `objectives.tsv` — (uuid, text)

The full uuid ↔ text registry for the course's pool, and the set that export's
prefix computation and load's token resolution run against. It anchors the **full**
uuid of every objective — including ones that are pooled but cover nothing (and so
have no `coverage.tsv` row), which `plan.md`'s abbreviated tokens alone could not
reconstruct. On export: dump every pooled objective's `(uuid, text)`. On load:
seed `objectives`, then resolve `plan.md` tokens against it.

### `coverage.tsv` — (uuid, hierarchy_id, node_id)

The many-to-many coverage edges that cannot be inline in any one human file: one
objective can cover nodes in **several** reference hierarchies (a CED leaf *and* a
book subsection *and* an IB statement). It carries coverage into the **reference**
hierarchies. Coverage into the **outline** (a lesson placement) is *not* duplicated
here — it lives structurally in `plan.md` as bullet-under-lesson. (Stated as a
rule: `coverage.tsv` holds every `coverage` row whose `hierarchy_id` is not this
course's outline slug.)

## Library shape (this repo)

### Moves in from `hierarchy-extractors`

- **`hierarchy.py`** — the parser, `LEVEL_TAGS`, `FLAVOR_KIND`, front-matter
  parsing, flavor detection, `parse_sections` / `section_text` / `to_nodes`.
  Brought in verbatim, then trimmed: the **`course` flavor's bullet handling is
  re-homed** into the new plan reader (the reference flavors stay generic).
- The **format spec** — the relevant sections of `hierarchy-extractors/HIERARCHIES.md`
  become a `FORMAT.md` (or a section of the README) here. This file, not the
  extractor's, is now the authoritative format definition. `json-format.md` is
  retired.

### Rewritten

- **`load_nodes.py`** — drops `json.load` and the node-list version check; instead
  reads a markdown file, runs the local `hierarchy.to_nodes(text)`, and feeds the
  resulting node list to the existing `build_rows` / `load`. The `FLAVOR_META`
  course/kind/slug policy and the `hierarchies`/`courses` upsert stay. (Most of the
  file is unchanged — only the *front door* swaps JSON for markdown.)

- **A new plan module** (`plan_io.py`, say) — the read/write half that has no
  analog today:

  - `write_course(conn, course, course_dir)` — renders `plan.md` (front-matter
    wiring + units → lessons → placed bullets + pool + LO lines) and dumps
    `objectives.tsv` / `coverage.tsv`. *Does not* touch reference `.md` files.

  - `read_course(conn, course_dir)` — loads reference `.md` (→ `nodes`,
    editable=0), parses `plan.md` (→ outline `nodes` editable=1 + objective
    interning + placement coverage + `node_attr` + the `courses` row + primary
    pointers + `hierarchy_targets`), and loads the two TSVs (→ `objectives`,
    `course_objectives`, reference `coverage`).

- **`seed.py` + `rebuild_db.py`** — collapse into a **corpus loader** that walks
  `courses/*/` and calls `read_course` per directory. No `manifest.toml`: course
  id/title/wiring now come from `plan.md` front matter, and the file set is the
  directory contents. `rebuild_db.py`'s "delete db, apply schema, reload" wrapper
  stays; its input becomes the corpus dir instead of JSON files + an export dir.

### Deleted

- `export_planning.py`, `import_planning.py`, and the `export/` TSV snapshots
  (replaced by the corpus + the two normalized TSVs).
- The node-list JSON examples (`examples/widgets-hierarchy.json`) and
  `examples/seed/manifest.toml`.
- This repo's dependence on `hierarchy-extractors/json-format.md` and on
  `build_hierarchy_json.py`.

### Kept, with a note

- **`render_outline.py`** stays as the **deliverable report** (units → lessons →
  objectives *with inline coverage tags*, traceability appendix, gap list). That is
  a generated, one-way *report*, distinct from the round-trippable `plan.md`
  *storage*. The two share the units→lessons→objectives walk; factor the shared
  walk so the report = storage body + appendices, but keep them as separate
  outputs. (The report reads coverage from the DB; the storage form keeps coverage
  in `coverage.tsv`.)
- **`course_bundle.py`** (whole-course JSON export/import) is orthogonal portability
  and can stay as-is. Optionally retarget it later to zip a course *directory*
  instead of a JSON blob; out of scope here.

## App changes (`app.py`)

The DB-facing route bodies are unchanged; only the file-I/O edges move:

- **Reference upload** (`/<course>/hierarchy/load`, `/<course>/h/<h>/upload`) now
  accepts a **markdown** file (not JSON), and **persists it into the course
  directory** as `<slug>.md` so the corpus stays complete — then loads it via the
  rewritten `load_nodes`.
- **Per-course export** (`/<course>/export`) writes the course directory
  (`plan.md` + the two TSVs) via `plan_io.write_course` instead of dumping TSVs.
- **Restore / Settings** (`/data/restore`) reads the corpus directory via the
  corpus loader instead of `rebuild_db.populate` over JSON specs.
- **`/<course>/outline.md`** becomes (or aliases) the canonical `plan.md` writer;
  the rich report keeps its own route.
- Startup: load the corpus dir if present (the merged seed/export dir), replacing
  the `LESSON_SEED_DIR` manifest path.

## Cross-repo: `hierarchy-extractors` (separate PR)

This repo's change lands first (it can read the markdown the extractor already
emits). Then, in the extractor repo:

- **Delete `build_hierarchy_json.py` and `json-format.md`.** The cross-repo
  contract is now "conforming markdown," defined by *this* repo's format doc.
- `hierarchy.py` stays there too — `build_hierarchy_xml.py` and
  `build_hierarchy_db.py` (the XML/HTML rendering path) still parse markdown. The
  duplication is acceptable under the same "the contract is data, not a shared
  module" philosophy as before — only the data interchange flips from JSON to
  markdown. The extractor's copy can drop the `course` flavor (it never produces
  or renders an outline).
- Update `CLAUDE.md` / `HIERARCHIES.md`: the extractor **produces** conforming
  markdown; the format is **owned by lesson-planning**; remove the
  "lesson-planning consumes our JSON" framing and the "owns `hierarchy.py`" claim.

## Phasing

1. **Format doc + parser move.** Bring `hierarchy.py` in, write `FORMAT.md`,
   rewrite `load_nodes.py` to parse markdown. Verify a reference `.md` loads into
   `nodes` exactly as the JSON path did (diff the resulting rows).

2. **Plan I/O.** Build `plan_io.write_course` / `read_course` and the two-TSV
   dump/load. Replace `export_planning.py` / `import_planning.py`. Verify a
   load → export → load round-trip is a fixpoint (byte-stable `plan.md` + TSVs).

3. **Corpus loader + app rewiring.** Collapse `seed.py` / `rebuild_db.py` into the
   corpus loader; switch the app's upload/export/restore/startup edges; drop
   `manifest.toml`.

4. **Examples + docs + deletions.** Convert `examples/` to a `courses/widgets/`
   directory; update `README.md` and `CLAUDE.md`; delete `export/`, the JSON
   examples, and the dead scripts.

5. **Extractor repo PR.** Land the producer-side changes above.

Each phase is independently verifiable; phases 1–2 can ship behind the existing
app without UI changes, since the DB schema never moves.

## Open / deferred

- **Token churn vs. stability.** Shortest-unique-prefix tokens can lengthen (and
  thus change) when a colliding objective is added, producing some `plan.md` diff
  noise. The per-objective floor blunts this; a uniform max-length token across the
  whole file is the more-stable-but-longer alternative if churn proves annoying.
  Residual hazard: a human who hand-mangles a token into ambiguity silently
  re-interns that objective (load falls back to text matching) — acceptable, since
  the token is meant to be left alone.
- **One outline per course directory.** The schema allows multiple `editable=1`
  hierarchies; this plan ships a single `plan.md` per course (the primary outline).
  Multiple authored outlines (e.g. a planned book) is a later extension — another
  `course`-flavor file plus a way to disambiguate which front matter owns the
  course wiring.
- **Re-emitting reference markdown.** References are load-only inputs here. If we
  ever want the app to *edit* a reference, we'd need a generic flavor-aware
  `nodes → markdown` writer (reconstructing flavor-specific level-1 headings),
  which this plan deliberately avoids.
- **`course_bundle.py`** could later target a zipped course directory instead of a
  JSON blob, unifying it with the corpus model.
