# The course markdown format

This repo stores a course as **human-editable markdown** (plus two small
normalized TSVs), and uses SQLite only as a cache it loads from and exports back
to those files. This document is the authoritative definition of that on-disk
format — it is owned here, and the companion `hierarchy-extractors` repo's job is
to **produce conforming reference markdown** in the *source form* (see
[Reference hierarchy markdown](#reference-hierarchy-markdown): no `slug:`; the app
assigns identity on upload).

See `plans/markdown-as-storage.md` for the design rationale, and `plan_io.py` /
`hierarchy.py` / `load_nodes.py` for the implementation.

## The courses directory and a course directory

A **courses directory** is a directory whose immediate subdirectories are each one course.
One course directory holds everything that describes that course:

```
my-courses/              # the courses root (a git repo; load source AND export target)
  widgets/
    ced.md               # a reference hierarchy — load-only input
    plan.md              # the outline + course wiring in front matter
    objectives.tsv       # (uuid, text)                 — machine-maintained
    coverage.tsv         # (uuid, hierarchy_id, node_id) — machine-maintained
    lessons/             # one markdown file per outline lesson (the lesson plans)
      what-is-a-widget-c37d8baf.md
```

Four file roles:

- **Reference hierarchy markdown** — one file per external standard (a CED, an IB
  syllabus, a book). Loaded read-only (`hierarchies.editable = 0`); the app and
  CLI never rewrite these.

- **The outline `plan.md`** — the course's authored lesson plan (units → lessons →
  objective bullets). The one hierarchy file the app writes
  (`hierarchies.editable = 1`); its front matter additionally carries the
  **course-level wiring**.

- **The two TSVs** — the normalized connective tissue (objective identity and the
  many-to-many coverage that cannot live inline in any one file).

- **The `lessons/` directory** — one markdown file per outline lesson, holding that
  lesson plan's free-text parts (see [Lesson plans](#lesson-plans)). A lesson is a
  first-class entity with a stable uuid; the file is its content, the `plan.md`
  heading its place in the outline.

### Slugs (hierarchy identity)

A hierarchy is identified by **(course, slug)**. The slug is **course-relative
("bare")** — unique only within its course — so the on-disk form carries no course
prefix and the same file can be dropped into two courses. In a course directory it
is **pinned** in the front matter's `slug:` key (authoritative on load), and by
convention the file is named `{slug}.md` (`ced.md` → `ced`, `plan.md` → `plan`). If
`slug:` is absent the filename stem is used; if the stem and a pinned `slug:`
disagree, the loader warns (rename the file to match — never edit the slug, which
is the identity coverage keys on). `coverage.tsv`, the outline's `targets:`, and
`primary_outline:` all use these bare slugs; the database scopes them to the
course.

## Reference hierarchy markdown

A reference exists in **two forms** that differ by exactly one field — the `slug:`:

- **Source form** — what an extractor produces or you hand-author *for upload*. It
  declares content and content metadata (`levels:`, `title:`) but **no `slug:`**: a
  hierarchy's identity within a course is assigned when the file is placed into
  one, so a source file is course-agnostic and portable. This is the form
  `hierarchy-extractors` emits.
- **Stored form** — what the app writes into a course directory after an
  upload, and reloads on startup. It is the source form **plus a pinned `slug:`**
  (the bare, course-relative identity), with the file named `{slug}.md`.

Uploading converts source → stored: the app assigns the bare slug (defaulting from
the filename stem, editable on the confirm screen) and pins it into the saved
front matter. The parser accepts **either** form — `slug:` is optional — so a
hand-placed source file with no `slug:` still loads, falling back to its filename
stem. Everything below describes the body + content metadata, which is identical
in both forms.

A `---` front-matter block (carrying the required `levels:` key) followed by ATX
headings whose depth encodes tree depth. Each heading is `ID␠TEXT` (the id is a
whitespace-free token); body lines under a heading belong to that node. There is
no "flavor": the level-1 id is read by a small list of heading patterns, then a
generic `# ID TEXT` fallback (id = first token, like every deeper level), so a new
format needs no special casing:

| Level-1 heading              | Level-1 id      | Deeper levels                  |
|------------------------------|-----------------|--------------------------------|
| `# Unit N: TITLE`            | `N`             | `## 1.1 …`, `### 1.1.A …`      |
| `# Big Idea N: TITLE (CODE)` | the `(CODE)`    | `## CRD-1 …`, `### CRD-1.A …`  |
| `# Chapter N: TITLE`         | `N`             | `## N.M …`, `### N.M.K …`      |
| `# Theme X: TITLE`           | `X`             | `## A1 …`, `### A1.1 …`        |
| `# ID TITLE` (any new shape) | `ID`            | `## ID …` (first token)        |

Front matter (a small YAML subset — scalars only). **Source form** (for upload —
no `slug:`):

```
---
levels: unit, topic, learning-objective, essential-knowledge
title: AP Computer Science A — 2025 CED
---
```

**Stored form** (in `my-courses/csa/ced.md`) is identical but adds the pinned slug:

```
---
slug: ced
levels: unit, topic, learning-objective, essential-knowledge
title: AP Computer Science A — 2025 CED
---
```

- `levels:` — **required**. A comma-separated list of the level **tag** names in
  depth order: `levels[i]` names heading depth *i+1* (so `levels: unit, lab, page`
  tags `#` nodes `unit`, `##` nodes `lab`, `###` nodes `page`). Each node's tag is
  stored as its `level`. The producer knows its own vocabulary, so it states it
  here. A heading nested deeper than the declared levels is an error.
- `title:` — **required**. The hierarchy's human label, shown in the sidebar.
- `slug:` — **stored form only**. The bare, course-relative identity (see Slugs
  above) — also the only label a reference carries (shown as a pill). Assigned and
  pinned by the app on upload; the filename matches (`{slug}.md`). A source file
  should omit it; if present, the app treats it as the default slug suggestion
  (still editable on the confirm screen).

Ids are kept verbatim and treated as opaque. (The outline `plan.md` is parsed
separately and does **not** take a `levels:` key — its levels are always unit /
lesson.)

## The outline: `plan.md`

`plan.md` is the `course` flavor (`# Unit: …` → `## Lesson title` → `- objective`
bullets) extended in three ways. The file's presence of a `course:` front-matter
key is what marks it as the course descriptor/outline.

A **unit** heading carries no id — `# Unit: TITLE` — and its positional id (`1`,
`2`, …) is regenerated from heading order on each load. A **lesson** heading is
`## TITLE` plus a trailing **identity token** `(#abcd)` (the same abbreviated-uuid
token objectives use): the lesson is a first-class entity with a stable uuid (its
content lives in `lessons/`, see [Lesson plans](#lesson-plans)), and the token is
how the heading points at it. A legacy `# Unit N:` / `## N.M …` heading is still
read (its number discarded), and a tokenless `## TITLE` is read as a new lesson
(it gets a fresh uuid + lesson file on the next save).

```markdown
---
course: widgets
title: Intro to Widgets
primary_outline: plan
targets: widgets-ced
---

# Unit: Widget Basics

## What Is a Widget (#c37d)

- Name the two main parts of a widget.  (#faf3)
- Explain what the frobnicator does.  (#221a)

# Unassigned lessons

## A lesson not yet in a unit (#9a02)

# Unplaced objectives

- Brainstorm a class project that uses widgets.  (#9eec)
```

Two trailing H1 sections are optional. **`# Unassigned lessons`** holds lessons
not (yet) under any unit — `## TITLE` entries with no parent, the round-trip home
for the outline's "Unassigned lessons" area (e.g. lessons left over after a unit
is deleted). **`# Unplaced objectives`** holds pool objectives not placed in any
lesson.

### 1. Front-matter wiring

Course-level facts that live in no single hierarchy:

- `course:` — the course id (also the `/<course>` URL). **Required**; its
  presence identifies this file as the outline.
- `title:` — the course's display title.
- `primary_outline:` — the outline's own slug (normally this file's stem).
- `calendar:` — (optional) the id of a bells calendar (a JSON file in the
  calendars directory, e.g. `bhs-2025-2026`) the calendar view lays the outline
  onto. The school-year span comes from that calendar (`firstDay`..`lastDay`),
  and **exam days** come from the calendar itself (any in-session day bells gives
  a non-class label, e.g. a named `EXAMS` schedule — rendered as red exam cells,
  not bookable lesson days). The calendar's first-class `annotations` field
  supplies the rest: an `apExams` range (under `annotations.ranges`) badges the
  weeks it overlaps "AP exams", and `annotations.weeks` grading-close entries badge
  their week "`<name>` close". These are read via the bells annotation API and are
  optional.
- `targets:` — a comma-separated list of reference slugs the outline is measured
  against (the `hierarchy_targets` rows).

### 2. Per-lesson learning objective

The learning objective is **part of the lesson plan**, so it lives in the lesson
file (the `## Learning objective` section — see [Lesson plans](#lesson-plans)), not
in `plan.md`. It is still stored as the lesson's `learning_objective` node
attribute (which the outline and calendar read), just sourced from the lesson file.
A legacy `**Learning objective:** …` line under a `plan.md` lesson heading is still
read on load and migrated into the lesson file on the next save.

### 3. Objective bullets are placements

A column-0 bullet under a lesson is a **raw objective placed in that lesson** (not
a node): it is interned into the objective pool and given a coverage edge to the
lesson. A `# Unplaced objectives` section (a level-1 heading whose text starts
with “Unplaced”) holds pooled objectives not yet placed in any lesson; the legacy
`## Pool …` level-2 heading is still read on load. Document order of all bullets
is the **master pool order** (`course_objectives.position`).

The bullet order **within a single lesson** (or a unit's rough zone) is that
node's own **per-node order** (`coverage.position`) — independent of the master
pool order. So an objective can sit third in the master list yet first in its
lesson; both orders round-trip (the master from overall document order, the
per-node from the order of bullets under each heading).

The outline hierarchy's own nodes are therefore only **units and lessons**. A
**unit** has a positional id (`1`, `2`, …) regenerated from heading order each load
— the markdown carries its title only. A **lesson** has a **stable uuid** node id,
carried by the heading's `(#token)` and pinned in its lesson file, so a lesson's
content survives reordering or retitling (its placements, being structural bullets,
round-trip as before).

### 4. Durations

Any node's heading may end with a **duration tag**: `(N weeks)`, `(N days)`, or
`(N hours)` (`N` an integer or decimal). It is stripped off the stored title and
kept in `node_duration`, then re-emitted on save.

- In the **outline**, units carry weeks and lessons carry days
  (`# Unit: Selection (2 weeks)`, `## Hello, world (3 days) (#abcd)`); these drive
  the calendar view. A lesson with no tag is one day (`(1 day)` is the default and
  is never written). On a lesson heading the duration tag sits just **before** the
  identity token, which is always the final group.
- In a **reference**, the tag rides the node heading too — the IB syllabi already
  use it (`## A1 Computer fundamentals (18 hours)`). Reference durations are stored
  for reporting, not laid on the calendar.

The tag is the **last** parenthesized group on the line (on a lesson heading, the
last one before the identity token), and only when it matches
`(<number> weeks|days|hours)` — an incidental `(HL only)` in a title is left alone.

## Lesson plans

Each outline lesson is a **lesson plan**: a first-class entity stored as its own
markdown file under the course's `lessons/` directory. The file holds the lesson
plan's free-text content, organized into a fixed set of **parts**; the lesson's
placement in the outline (its unit, order, and the objectives placed in it) stays
in `plan.md`. The lesson plan is a *distillation* of those placed objectives, so
they are shown alongside it (in the editor) but not duplicated into the file.

The filename is `lessons/<slug>-<shortid>.md`, where `<slug>` is the title
slugified (lowercase, non-alphanumerics → `-`) and `<shortid>` is the first 8 hex
chars of the uuid. The filename is **cosmetic**: identity is the front-matter
`uuid:`. Two lessons in one course may share a title (the uuids differ), unlike
objectives, which are interned by text.

```markdown
---
uuid: c37d8baf-39d2-4688-9b18-68a23164a510
title: What Is a Widget
---

## Preview

A quick hook to get students thinking about widgets.

## Learning objective

Describe a widget and name its parts.

## Key ideas

…
```

- **`uuid:`** — the lesson's identity (authoritative, pinned). The `plan.md` lesson
  heading's `(#token)` resolves to it by shortest-unique prefix, exactly as an
  objective bullet's token resolves against `objectives.tsv`. A tokenless heading
  mints a fresh uuid and a new lesson file.
- **`title:`** — a mirror of the `plan.md` heading (the authoritative title), kept
  in sync on save; the filename slug derives from it.
- **The body** is the nine parts as `## <heading>` sections, in this canonical
  order, **only the non-empty ones written**: **Preview**, **Learning objective**,
  **Review**, **Key ideas**, **Expert thinking**, **Guided practice**, **Closure**,
  **Independent practice**, **Summation**. Each is free-text markdown. Only these
  nine headings delimit parts — any other heading is content of the current part, so
  a part may contain its own sub-headings. Each part is stored as a `node_attr` row
  on the lesson (`learning_objective` is the very attribute the outline/calendar
  already read).

On save, the `lessons/` directory is **reconciled**: a renamed lesson's file is
rewritten under its new slug and the stale name removed; a lesson deleted from the
outline has its file deleted.

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
  *within this course* (reuse the course's objective with that text, else mint a
  fresh uuid). This is how a hand-typed bullet, or a cleared token, becomes a new
  objective. Objectives are course-owned: identical text in another course is a
  separate objective, and a uuid already owned by another course is re-minted on
  load.

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
