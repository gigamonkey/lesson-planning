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
  courses directory and rebuild the database from scratch.
- Live **coverage**: as you place objectives, every leaf of the standard reads as
  planned, objective-only, or a gap, with a gaps-only filter.

The only third-party dependency is **Flask**. Requires Python ≥ 3.13; scripts run
with [`uv`](https://docs.astral.sh/uv/).

## Quick start

A synthetic example course ("Intro to Widgets") lives in `examples/widgets/` —
a **course directory**: its reference hierarchy markdown (`widgets-ced.md`), the
outline (`plan.md`), and the normalized `objectives.tsv` / `coverage.tsv`. Use the
`examples/` courses directory to see the whole pipeline end to end:

```bash
# Rebuild a database from the markdown courses directory (a dir of course directories).
uv run rebuild_db.py --courses examples            # -> db.db
```

To load a single hierarchy markdown file straight into a database (the lower-level
step `rebuild_db` orchestrates):

```bash
uv run load_nodes.py examples/widgets/widgets-ced.md db.db \
    --course widgets --hierarchy widgets-ced --course-title "Intro to Widgets"
```

Or do it all in the browser:

```bash
LESSON_COURSES_DIR=examples uv run app.py          # http://localhost:5001
```

The app boots an empty database from `schema.sql`, then loads any course in the
courses directory. Without a courses directory, create a course with **+** in the sidebar and
upload a hierarchy markdown file with the **+** in its sidebar header.

## The format

A course is a directory of markdown + two TSVs; the courses directory is a directory of
those. The full spec — reference hierarchy markdown, the `plan.md` outline
profile, and the two TSVs — is in [`FORMAT.md`](FORMAT.md). A reference hierarchy
declares its level names (`levels:`) and `title:` in front matter; heading depth
encodes tree depth and each heading carries a verbatim id as its first token (the
level-1 id via a small pattern list + a generic fallback). This is the **source
form** you upload — the app assigns the bare `slug:` (its course-relative identity)
on upload and pins it into the stored copy:

```markdown
---
levels: unit, topic, learning-objective, essential-knowledge
title: Intro to Widgets — Reference
---
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
**courses directory**: a git repo whose top-level directories are course directories of
markdown + TSVs. The app **autosaves + commits** your edits back to it; rebuild
reproduces the database from it:

```bash
uv run rebuild_db.py --courses ../bhs-cs-courses  # rebuild db.db from a courses directory
uv run seed.py --all ../bhs-cs-courses db.db     # reload every course (non-destructive)
```

(Single-user mode requires the courses directory to be a git repo and commits there
automatically — there is no manual Export button; the **Settings** page offers a
global **Sync** to re-read external edits / a `git pull`. Reference hierarchy
markdown is a load-only input and is never rewritten — only `plan.md` and the two
TSVs are.)

## Setting up courses in the app

Setup is driven from the sidebar:

- **+** next to the title creates a course (id + title) — or imports one from a
  bundle file.

- each course's sidebar block holds its controls: the **+** adds a reference
  hierarchy (upload hierarchy **markdown**, saved into git); the title is
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

## Running & the courses directory

Point the app at a **courses directory** — a directory whose subdirectories are course
directories — via `LESSON_COURSES_DIR`, and it populates a blank database
automatically. In single-user mode the courses directory must be a **git repo** (a checkout
of your courses repo): edits autosave + commit there, on the checked-out branch,
with no remote push. `serve.sh` defaults it to a sibling `../bhs-cs-courses`
checkout when present.

```bash
LESSON_COURSES_DIR=../bhs-cs-courses uv run app.py   # or just ./serve.sh -d
```

To try it without a courses repo, point it at the bundled synthetic demo. A plain
(non-repo) directory is copied into a **throwaway git repo** at startup, so edits
still autosave + commit — just to disposable git, discarded when you're done:

```bash
LESSON_COURSES_DIR=examples uv run app.py        # the "Intro to Widgets" demo
```

Each course directory carries everything the loader needs (its `plan.md` front
matter names the course; see [`FORMAT.md`](FORMAT.md)), so no manifest is needed.
Seeding is **create-if-absent per course**, so it's safe on every restart
(existing courses are left untouched). The same thing from a terminal:

```bash
uv run seed.py ../bhs-cs-courses db.db          # load new courses
uv run seed.py --all ../bhs-cs-courses db.db    # reload every course
```
