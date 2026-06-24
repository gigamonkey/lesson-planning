# The course markdown format

This repo stores a course as **human-editable markdown** (plus two small
normalized TSVs), and uses SQLite only as a cache it loads from and exports back
to those files. This document is the authoritative definition of that on-disk
format — it is owned here, and the companion `hierarchy-extractors` repo's job is
to **produce conforming reference markdown**.

See `plans/markdown-as-storage.md` for the design rationale, and `plan_io.py` /
`hierarchy.py` / `load_nodes.py` for the implementation.

## The corpus and a course directory

A **corpus** is a directory whose immediate subdirectories are each one course.
One course directory holds everything that describes that course:

```
courses/                 # the corpus root (load source AND export target)
  widgets/
    widgets-ced.md       # a reference hierarchy (csa flavor) — load-only input
    plan.md              # the outline (course flavor) + course wiring in front matter
    objectives.tsv       # (uuid, text)                 — machine-maintained
    coverage.tsv         # (uuid, hierarchy_id, node_id) — machine-maintained
```

Three file roles:

- **Reference hierarchy markdown** — one file per external standard (a CED, an IB
  syllabus, a book), in its flavor's heading shape. Loaded read-only
  (`hierarchies.editable = 0`); the app and CLI never rewrite these.

- **The outline `plan.md`** — the course's authored lesson plan, in the `course`
  flavor. The one hierarchy file the app writes (`hierarchies.editable = 1`). Its
  front matter additionally carries the **course-level wiring**.

- **The two TSVs** — the normalized connective tissue (objective identity and the
  many-to-many coverage that cannot live inline in any one file).

### Slugs

Each hierarchy's id (`hierarchy_id` / the `nodes.hierarchy` column / the keys in
`coverage.tsv` and the target list) is the markdown file's **filename stem**:
`widgets-ced.md` → `widgets-ced`, `plan.md` → `plan`. A front-matter `slug:`
overrides it.

## Reference hierarchy markdown

An optional `---` front-matter block followed by ATX headings whose depth encodes
tree depth. Each heading is `ID␠TEXT` (the id is a whitespace-free token); body
lines under a heading belong to that node. The level-1 heading both names the
root and signals the **flavor**:

| Flavor   | Level 1                      | Level 2                       | Level 3                  | Level 4                   | Level 5            |
|----------|------------------------------|-------------------------------|--------------------------|---------------------------|--------------------|
| `csa`    | `# Unit N: TITLE`            | `## 1.1 …` topic              | `### 1.1.A …` LO         | `#### 1.1.A.1 …` EK        | —                  |
| `csp`    | `# Big Idea N: TITLE (CODE)` | `## CRD-1 …` EU               | `### CRD-1.A …` LO       | `#### CRD-1.A.1 …` EK      | —                  |
| `book`   | `# Chapter N: TITLE`         | `## N.M …` section            | `### N.M.K …` subsection | —                         | —                  |
| `ib`     | `# Theme X: TITLE`           | `## A1 …` topic               | `### A1.1 …` subtopic    | `#### A1.1.1 …` statement  | `##### A1.1.1.1 …` |

Front matter (a small YAML subset — scalars only):

```
---
title: AP Computer Science A — 2025 CED
kind: ced
---
```

- `title:` — a human title for the hierarchy (optional).
- `kind:` — what the hierarchy *is* (`ced`, `syllabus`, `book`, …). Defaults per
  flavor (`csa`/`csp` → `ced`, `ib` → `syllabus`, `book` → `book`).
- `slug:` — overrides the filename-stem hierarchy id (optional).

The flavor's per-level **tag** (`unit`, `topic`, `learning-objective`, …) is
resolved by the parser and stored as the node's `level`. Ids are kept verbatim
and treated as opaque.

## The outline: `plan.md`

`plan.md` is the `course` flavor (`# Unit: …` → `## Lesson title` → `- objective`
bullets) extended in three ways. The file's presence of a `course:` front-matter
key is what marks it as the course descriptor/outline.

Unlike a reference hierarchy, the outline's headings carry **no ids** — a unit is
`# Unit: TITLE` and a lesson is `## TITLE` (the title alone). The positional ids
(`1`, `1.1`, …) are regenerated from heading order on each load (see §3 below), so
they are never written. A legacy `# Unit N:` / `## N.M …` heading is still read
(its number discarded), but exports always use the id-free form.

```markdown
---
course: widgets
title: Intro to Widgets
primary_outline: plan
targets: widgets-ced
---

# Unit: Widget Basics

## What Is a Widget

**Learning objective:** Describe a widget and name its parts.

- Name the two main parts of a widget.  (#faf3)
- Explain what the frobnicator does.  (#221a)

## Pool — not yet placed

- Brainstorm a class project that uses widgets.  (#9eec)
```

### 1. Front-matter wiring

Course-level facts that live in no single hierarchy:

- `course:` — the course id (also the `/<course>` URL). **Required**; its
  presence identifies this file as the outline.
- `title:` — the course's display title.
- `primary_outline:` — the outline's own slug (normally this file's stem).
- `calendar:` — (optional) the id of a bells calendar (a JSON file in the
  calendars directory, e.g. `bhs-2025-2026`) the calendar view lays the outline
  onto. The school-year span comes from that calendar (`firstDay`..`lastDay`).
- `targets:` — a comma-separated list of reference slugs the outline is measured
  against (the `hierarchy_targets` rows).

### 2. Per-lesson learning objective

A `**Learning objective:** …` line directly under a lesson heading. Stored as the
lesson's `learning_objective` node attribute.

### 3. Objective bullets are placements

A column-0 bullet under a lesson is a **raw objective placed in that lesson** (not
a node): it is interned into the objective pool and given a coverage edge to the
lesson. A `## Pool …` section (a level-2 heading whose text starts with “Pool”)
holds pooled objectives not yet placed in any lesson. Document order of all
bullets is the **master pool order** (`course_objectives.position`).

The bullet order **within a single lesson** (or a unit's rough zone) is that
node's own **per-node order** (`coverage.position`) — independent of the master
pool order. So an objective can sit third in the master list yet first in its
lesson; both orders round-trip (the master from overall document order, the
per-node from the order of bullets under each heading).

The outline hierarchy's own nodes are therefore only **units and lessons**, with
positional ids (`1`, `1.1`, `1.2`, …) regenerated from heading order on each load
— the markdown carries titles only. Placement and the learning objective are
structural (they sit under their headings), so they survive an export → reload
round-trip even as positional ids are renumbered.

### 4. Durations

Any node's heading may end with a **duration tag**: `(N weeks)`, `(N days)`, or
`(N hours)` (`N` an integer or decimal). It is stripped off the stored title and
kept in `node_duration`, then re-emitted on save.

- In the **outline**, units carry weeks and lessons carry days
  (`# Unit: Selection (2 weeks)`, `## Hello, world (3 days)`); these drive the
  calendar view. A lesson with no tag is one day (`(1 day)` is the default and is
  never written).
- In a **reference**, the tag rides the node heading too — the IB syllabi already
  use it (`## A1 Computer fundamentals (18 hours)`). Reference durations are stored
  for reporting, not laid on the calendar.

Only the **last** parenthesized group on the line is the tag, and only when it
matches `(<number> weeks|days|hours)` — an incidental `(HL only)` in a title is
left alone.

## Objective identity — abbreviated uuid tokens

Each objective bullet ends with an **identity token** `(#<prefix>)`: the shortest
prefix of the objective's uuid that is unique among the course's objectives (at
least 4 hex chars), with a `#` sigil so it is never confused with literal parens
in the text. Only the *trailing* `(#[0-9a-f]+)` group is treated as a token.

On **load**, the token is resolved by prefix-matching against the uuids in
`objectives.tsv`:

- Resolves to exactly one uuid → that objective; the bullet's text is adopted as
  the objective's text (so a reword preserves identity, and therefore coverage).
- No token, or a token matching zero or more than one uuid → intern by exact text
  (reuse an objective with that text, else mint a fresh uuid). This is how a
  hand-typed bullet, or a cleared token, becomes a new objective.

**No full uuids ever appear in markdown.**

## `objectives.tsv` — (uuid, text)

The full uuid ↔ text registry for the course's pool, sorted by uuid for stable
diffs. It anchors the full uuid of every objective — including pooled ones that
cover nothing (and so have no `coverage.tsv` row) — and is the set that token
resolution and export's prefix computation run against.

## `coverage.tsv` — (uuid, hierarchy_id, node_id)

The many-to-many coverage edges into the **reference** hierarchies (one objective
can cover nodes in several). Outline placement is *not* duplicated here — it lives
structurally in `plan.md`. (Rule: `coverage.tsv` holds every coverage row whose
`hierarchy_id` is not the course's outline.)

Rows are sorted by `(hierarchy_id, node_id, position)`, and the **row order within
each `(hierarchy_id, node_id)` group is that node's per-node objective order**
(`coverage.position`) — there is no explicit position column; the order is carried
by the rows' sequence and re-derived (by encounter order per node) on load.
