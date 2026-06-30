# lesson-planning

A course-agnostic tool for turning a curriculum **hierarchy** into a traceable
**lesson plan**. You supply the hierarchy and the learning objectives; nothing
about any specific course is baked in. See `README.md` for the user-facing
overview and `plans/` for the design history.

A course lives on disk as **human-editable markdown** + two normalized TSVs; the
SQLite database is a cache loaded from / exported back to those files. **This repo
owns the markdown format** (`hierarchy.py` + `FORMAT.md`); the companion
`hierarchy-extractors` repo's job is to produce conforming *reference* markdown
from sources out of our control (PDFs, PreTeXt books). See `FORMAT.md` for the
format and `plans/markdown-as-storage.md` for the design.

## Tech Stack

- Python 3.13; third-party runtime deps: Flask, `bell-schedule` (the
  school-calendar library, import name `bells`), `bhs-calendars` (its bundled
  calendar data) — both PyPI packages now — and `markdown` (renders lesson-plan
  part content to HTML in the lesson view). Add a `[tool.uv.sources]` path/editable
  source for either calendar package to develop it alongside this app.
- SQLite as a cache; a git-tracked **courses directory** of markdown + TSVs is the committed
  state
- Package manager: `uv` (run scripts with `uv run <script>.py`)
- Frontend is server-rendered + htmx (CDN), **no build step except** the outline
  Markdown editor: CodeMirror 6 bundled by esbuild (`npm run build`) into the
  committed `static/editor.bundle.js`. The committed bundle keeps the Python
  runtime node-free; only rebuilding the editor needs `npm install`.

## Project Structure

- `*.py` — the engine scripts (see Key Scripts below)
- `app.py` + `templates/` + `static/` — the Flask app
- `frontend/editor.js` + `package.json` — CodeMirror 6 source for the outline
  editor, bundled to `static/editor.bundle.js` (committed) via `npm run build`
- `hierarchy.py` — the curriculum-hierarchy markdown parser (this repo owns it);
  `FORMAT.md` — the on-disk format spec
- `schema.sql` — canonical schema; `db.db` — live working copy (gitignored). There
  is **no in-repo `courses/` default courses directory** — single-user mode requires
  `LESSON_COURSES_DIR` to point at a git repo (a courses-repo checkout, e.g. the
  sibling `../bhs-cs-courses`); collab mode uses the git clone on the volume. A
  plain (non-repo) dir like `examples/` is copied into a throwaway git repo at
  startup so the demo still autosaves to files + can be Saved (committed).
- `examples/` — a courses directory with one synthetic course, `examples/widgets/`:
  `widgets-ced.md` (reference hierarchy markdown), `plan.md` (the outline +
  course wiring), `objectives.tsv`, `coverage.tsv`, and `lessons/` (one markdown
  file per lesson — the lesson plans)
- `plans/` — implementation plans (design record). Do **not** read `plans/done/`
  unless explicitly asked — those describe the code as it was when written.

## Key Scripts

| Script                 | Purpose                                                                                                                                                                  |
|------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `hierarchy.py`         | The curriculum-hierarchy **markdown parser** (this repo owns it): front-matter parsing, level-1 id extraction (`parse_root_id`: a small pattern list + a generic `# ID TEXT` fallback — no "flavor"), and `to_nodes` (markdown → flat, already-tagged node-list dict). Level names come from the required `levels:` front matter. Used by `load_nodes` (references) and `plan_io` (helpers). |
| `load_nodes.py`        | Parses a hierarchy **markdown** file (`hierarchy.to_nodes`) into the `nodes` table (keyed by `(course, hierarchy)`), registers the hierarchy and upserts its `course`. Slug/course default from the front-matter `slug:`/filename and flags; `apply_schema` applies `schema.sql`. `load_into` does it on a caller's connection. |
| `plan_io.py`           | Read/write a course as a directory: `read_course` (references + `plan.md` outline + the two TSVs + `lessons/*.md` → db) and `write_course` (db → `plan.md` + the two TSVs + `lessons/*.md`, reconciling renamed/deleted lesson files; reference markdown left untouched). `load_plan_text` loads an edited `plan.md` *text* (outline + pool only, tokens resolved against the live db; preserves lesson content) — the in-memory loader behind the web Markdown editor. Objective **and lesson** identity via abbreviated uuid tokens. Lessons are first-class: each is a uuid-identified `lessons/<slug>-<shortid>.md` holding the lesson plan's free-text parts (the learning objective + the rest); the `plan.md` lesson heading carries a `(#token)` resolving to its file. |
| `import_objectives.py` | Imports raw objectives into a course's pool, interning by `(course, text)` — objectives are course-owned (idempotent; `parse_coverage` + `upsert`); `copy_objectives` re-interns one course's pool into another. Plain-text (pool only) or TSV (`objective`/`text`, optional `hierarchy_id`/`node_id` coverage edge, optional `uuid`). No default coverage target — a row's hierarchy is its `hierarchy_id` (bare slug; course from context) or `--hierarchy`, else pool-only. `--replace` re-seeds. |
| `seed.py`              | Courses-directory loader: load each course directory in a courses directory (`plan_io.read_course`). `seed` skips courses already present (create-if-absent; run on startup); `load_courses`/`--all` reloads all. CLI: `uv run seed.py <courses-dir> [db]`. |
| `rebuild_db.py`        | One-command rebuild from scratch: delete db, apply `schema.sql`, load every course in the courses directory. `--courses <dir>` (default `courses`). |
| `course_bundle.py`     | Export/import a whole course as one self-contained JSON bundle (course + hierarchies + nodes/attrs + durations + objectives + coverage + targets). `export`/`import` subcommands; also wired into the app (per-course Setup export, sidebar import). Additive to the markdown courses directory. |
| `calendar_view.py`     | Pure layout engine for the calendar view: given a `bells.BellSchedule` + a course's outline (units→weeks, lessons→days), lays units across the year's *teaching weeks* (loose; breaks skipped) and lessons into school days, returning a view model. No Flask/SQL. `load_calendar(id, dir)` loads a bells JSON. **Pinned units** (a unit's `pin` `{edge,week}`, from `node_pin`) anchor a unit's start/end on a school-week number: the layout is segmented around pins, so units that don't fit before a pin overflow and slack before a pin shows as an Unplanned gap. |
| `validate.py`          | Internal-consistency checker for a course directory, on the **raw files** (no db) so it sees corruption before `read_course`'s lenient resolution hides it: every uuid slot is a real UUID (objectives.tsv/coverage.tsv `uuid`, each `lessons/*.md` `uuid:`), and every uuid reference resolves (coverage `uuid`→objective, plan.md bullet token→objective, lesson-heading token→lesson file, coverage `(hierarchy_id, node_id)`→reference node). `validate_course(dir)`→list of problem strings; `read_course` prints them as warnings on load; CLI `uv run validate.py <courses-dir>` checks a tree (non-zero exit if any). |

## Running

```bash
# Rebuild a db from a markdown courses directory (a dir of course directories).
uv run rebuild_db.py --courses <courses-dir>   # deletes db.db; default courses dir 'courses'

# Load a single hierarchy markdown file into a db (the step rebuild_db orchestrates).
uv run load_nodes.py <your-hierarchy>.md db.db --course <course>

# Export a course back to its courses directory (plan.md + the two TSVs + lessons/).
uv run python -c "import plan_io; plan_io.write_course('db.db', '<course>', '<courses-dir>/<course>')"

# Web app (port 5001): bootstraps an empty db from schema.sql, then loads the
# courses directory. Setup is sidebar-driven: the top "+" creates (or imports) a course;
# each course's controls live on its sidebar block -- "+" uploads a hierarchy
# markdown, the title is click-to-edit, per-reference star/trash set-primary/delete,
# and the course header has bundle-export + delete; the Settings page does global
# restore-from-courses-directory.
#
# ALWAYS run the server via serve.sh -d (detached, idempotent, listens on
# 0.0.0.0). It defaults LESSON_COURSES_DIR to the sibling ../bhs-cs-courses
# checkout when present -- which enables Local git mode (below). Single-user mode
# now REQUIRES LESSON_COURSES_DIR: a bare `uv run app.py` with none set exits with
# an error. A plain (non-repo) dir like examples/ runs as a throwaway-git demo.
./serve.sh -d                                  # detached on 0.0.0.0:5001; log: /tmp/lesson-planning.log
LESSON_COURSES_DIR=examples uv run app.py       # bundled widgets demo (throwaway git repo)
```

**Local git mode is now the only single-user mode.** `LESSON_COURSES_DIR` must be
the top of its own git repo (a checkout of the courses repo); single-user then
treats it like collab does: edits **autosave to the course FILES** there
(debounced; structural ops reify immediately), authored/committed as below.
A plain (non-repo) `LESSON_COURSES_DIR` (e.g. `examples/`) is copied into a
**throwaway git repo** at startup (`_ensure_courses_repo` in `app.py`, with a local
demo git identity) so the demo still autosaves + can be saved — to disposable git,
never into this engine repo. `serve.sh` defaults `LESSON_COURSES_DIR` to a sibling
`../bhs-cs-courses` checkout when present, else `examples`.

**Saving is decoupled from writing files** (see `plans/manual-save.md`). Editing
updates the db (a cache); a debounced timer writes the db out to the course files
(`schedule_autosave` → write-only `flush`, no commit) so disk tracks the db within
the debounce window (`LESSON_AUTOSAVE_SECONDS`, default 2). **Committing is the
explicit Save button** (sidebar): it reifies every course, prompts for a commit
message (pre-filled from the buffered edit phrases via `collab.suggest_message`),
commits, and — in collab mode — **pushes to GitHub immediately**; local-git/demo
just commits. Routes: `/save` (commit + push), `/flush` (write pending files now —
called via a `beforeunload` beacon), `/save/suggestion` (the dialog default),
`/savebar` (the polled Save-button + status fragment, `_savebar.html`). A dirty
flag (`collab.mark_dirty`/`is_dirty`, keyed by handle or `"_local"`) drives the
Commit button's "uncommitted" hint; `git status` (`collab.has_uncommitted`) is the
truth at Save/Sync time. **Sync/Reload** only pulls others' work: collab merges
`origin/main` (and refuses while there are uncommitted edits — Commit first);
single-user **Reload** re-reads every course from disk (lossless — it flushes
first). An **external-change guard** (`collab.remember_head`/`head_moved`/
`flag_conflict`, recorded whenever the db is built from a repo or we commit to it)
stops a `git pull` that moved HEAD under us from being clobbered: the autosave
skips, Commit refuses, and the conflict warning points at Reload, which takes the
disk version. `commit_repo` (the shared
stage-add-commit-maybe-push primitive) lives in `collab.py`; `app.py`'s
`_git_target()` picks per-mode (repo, db, author, key, delay), `apply_structural`
reifies structural ops to disk without committing, and `git_backed()` gates the
shared UI/behavior.

The outline workspace has an **"Edit as Markdown"** button (only on the editable
outline) opening a CodeMirror 6 editor (`/<course>/outline/edit`) on the
round-trippable `plan.md`. Saving posts to `/<course>/outline/source`, which runs
`plan_io.load_plan_text` then `plan_io.write_course` — so a save updates the db
**and** writes `plan.md` + the TSVs to disk, leaving the course clean.

Each lesson links to a **lesson view** (`/<course>/lesson/<uuid>`, `lesson_view` +
`templates/lesson.html`) — its nine free-text parts rendered from the lesson file's
`node_attr` (via `markdown`), plus the raw objectives placed in it (the plan
distills them). A "journal" icon opens it from the outline lesson card and from each
calendar lesson cell. An editor can edit the parts two ways: **per-part in place**
(click a part's pencil / an empty part's "+ add" to reveal a Markdown textarea;
Save htmx-posts to `lesson_part_save` → `node_attr`, swapping `_lessonpart.html`
back in) and **whole-file** ("Edit as Markdown" → `lesson_edit_md` opens a
CodeMirror editor on the nine `## part` sections, posting to `lesson_source` which
parses → `node_attr` → `write_course`). Both rely on the lesson file's stable uuid;
the file autosave writes it to disk and the Save button commits it as usual.

The **Calendar** sidebar item (`/<course>/calendar`) lays the outline across the
school year (units→weeks, lessons→days; see `calendar_view.py` and the duration
tags in `FORMAT.md`). It reads bells calendar JSONs from `LESSON_CALENDAR_DIR`
(default the data bundled in the `bhs-calendars` package); a course binds to one via the
`calendar:` key in its `plan.md` front matter (the year span comes from the
calendar's `firstDay`..`lastDay`). Exam days come from the calendar's own
`nonClassDays`; the AP-exam window and grading-period closes come from the
calendar's first-class `annotations` field (a `ranges.apExams` range and
`weeks.<n>` grading-close entries), read back per school week via the bells
annotation API (`annotations_for_week`) and the canonical school-week numbering
(`school_weeks`) — see `_week_badges` / `_weeks` in `calendar_view.py`. (This
replaced the old out-of-band `calendar-extras/` sidecar once bells ≥ 0.8 / the
`bhs-calendars` ≥ 2.10 data carried `annotations`.)

A unit can be **pinned** to a school week via a `(starts week N)` / `(ends week N)`
tag on its `# Unit:` heading (the last group, after the duration tag; see
`FORMAT.md` §5). The pin is stored in the `node_pin` table and consumed by
`build_calendar`, which anchors the unit on that week instead of flowing it
sequentially — units that can't fit before a pin overflow, and slack before a pin
becomes an Unplanned gap. Pins round-trip through `plan.md` (`hierarchy.split_pin`/
`format_pin`, `plan_io.parse_plan`/`write_course`); setting a pin is markdown-only
today (no dedicated UI button yet).

```bash
# Rebuild the editor bundle after editing frontend/editor.js (needs Node/npm).
npm install        # first time only
npm run build      # -> static/editor.bundle.js (committed)

# Run the plan_io / load_plan_text round-trip checks.
uv run test_plan_io.py

# Check course files for internal consistency (uuids well-formed + references resolve).
uv run validate.py <courses-dir>     # or test it: uv run test_validate.py
```

The `examples/` courses directory (`examples/widgets/`) is a drop-in example course — see
`README.md`.

## Markdown

When writing markdown include blank lines before and after lists and between list
items.
