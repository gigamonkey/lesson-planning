# lesson-planning

A small, course-agnostic tool for turning a curriculum **hierarchy** (units →
topics → objectives → knowledge statements, or whatever shape your standard
uses) into a **traceable lesson plan**. You bring your own hierarchy and your own
learning objectives; the tool helps you place objectives under the standard,
shows you what's covered and what's still a gap, and renders the finished plan as
markdown.

Nothing about any particular course is baked in. Point it at *your* hierarchy
and *your* objectives.

A course lives on disk as **human-editable markdown** (plus two small normalized
TSVs); SQLite is just a cache the tool loads from and exports back to those files.
The markdown format is defined here (see [`FORMAT.md`](FORMAT.md)). Reference
hierarchies can be hand-authored or produced by the companion
[hierarchy-extractors](../hierarchy-extractors) repo, whose job is to turn a
source out of your control (a CED PDF, the IB syllabus, a PreTeXt book) into
conforming markdown.

## What you get

- A web app (Flask) to load a hierarchy, place raw objectives onto it, author
  lessons, and watch coverage fill in — then **export** the course back to
  git-trackable markdown.
- A command-line pipeline for the same lifecycle: load a course from its markdown
  corpus, render the plan, rebuild the database from scratch.
- A **traceability** view: every leaf of the standard maps to the lesson(s) that
  cover it, plus an explicit list of gaps.

The only third-party dependency is **Flask**. Requires Python ≥ 3.13; scripts run
with [`uv`](https://docs.astral.sh/uv/).

## Quick start

A synthetic example course ("Intro to Widgets") lives in `examples/widgets/` —
a **course directory**: its reference hierarchy markdown (`widgets-ced.md`), the
outline (`plan.md`), and the normalized `objectives.tsv` / `coverage.tsv`. Use the
`examples/` corpus to see the whole pipeline end to end:

```bash
# 1. Rebuild a database from the markdown corpus (a dir of course directories).
uv run rebuild_db.py --corpus examples            # -> db.db

# 2. Render the plan (units → lessons, traceability appendix, gap list).
uv run render_outline.py db.db /tmp/plan.md --course widgets
```

To load a single hierarchy markdown file straight into a database (the lower-level
step `rebuild_db` orchestrates):

```bash
uv run load_nodes.py examples/widgets/widgets-ced.md db.db \
    --course widgets --hierarchy widgets-ced --course-title "Intro to Widgets"
```

Or do it all in the browser:

```bash
LESSON_CORPUS_DIR=examples uv run app.py          # http://localhost:5001
```

The app boots an empty database from `schema.sql`, then loads any course in the
corpus directory. Without a corpus, create a course with **+** in the sidebar and
upload a hierarchy markdown file with the **+** in its sidebar header.

## The format

A course is a directory of markdown + two TSVs; the corpus is a directory of
those. The full spec — reference hierarchy markdown (the `csa`/`csp`/`ib`/`book`
flavors), the `plan.md` outline profile, and the two TSVs — is in
[`FORMAT.md`](FORMAT.md). A reference hierarchy's level-1 heading names the top
level and the flavor, and deeper headings carry a verbatim id as their first
token:

```markdown
# Unit 1: Widget Basics
## 1.1 What Is a Widget
### 1.1.A Describe the parts of a widget
#### 1.1.A.1 A widget has a frobnicator and a sprocket.
```

The deepest nodes (here, the `####` knowledge statements) are the **leaves** —
the unit of coverage. Your lesson plan "covers" the standard when every leaf maps
to a lesson.

## Saving & version control

`db.db` is the live working copy and is gitignored. The committed state is the
**corpus**: a directory of course directories of markdown + TSVs. Export writes a
course back to it; rebuild reproduces the database from it:

```bash
uv run rebuild_db.py --corpus courses            # rebuild db.db from the corpus
uv run seed.py --all courses db.db               # reload every course (non-destructive)
```

(The app exposes per-course **Export** in the sidebar and a global **Restore from
version control** on its **Settings** page. Reference hierarchy markdown is a
load-only input and is never rewritten — only `plan.md` and the two TSVs are.)

## Setting up courses in the app

Setup is driven from the sidebar:

- **+** next to the title creates a course (id + title) — or imports one from a
  bundle file.

- each course's sidebar block holds its controls: the **+** adds a reference
  hierarchy (upload hierarchy **markdown**, saved into the corpus); the title is
  click-to-edit; each reference has a **★** (set primary, shown with more than one)
  and a trash; the course header has a **download** (export the whole course as a
  self-contained bundle file) and a trash (delete the course).

- the **Objectives** page seeds raw objectives into the course pool (plain text,
  one per line, or a TSV with an `objective` column). Categorizing an objective to
  a node happens on a hierarchy page (drag it onto the node), where the target
  hierarchy is unambiguous.

A course bundle round-trips everything (hierarchies, nodes, objectives, coverage,
the outline) so a course is portable and re-creatable; from a terminal:

```bash
uv run course_bundle.py export db.db <course> <course>.json
uv run course_bundle.py import db.db <course>.json [--as <new-id>]
```

## Seeding on startup

Point the app at a **corpus** — a directory whose subdirectories are course
directories — and it populates a blank database automatically. Set
`LESSON_CORPUS_DIR` (default `courses`):

```bash
LESSON_CORPUS_DIR=examples uv run app.py        # or set it before serve.sh
```

Each course directory carries everything the loader needs (its `plan.md` front
matter names the course; see [`FORMAT.md`](FORMAT.md)), so no manifest is needed.
Seeding is **create-if-absent per course**, so it's safe on every restart
(existing courses are left untouched). The same thing from a terminal:

```bash
uv run seed.py examples db.db                   # load new courses
uv run seed.py --all examples db.db             # reload every course
```
