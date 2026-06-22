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

- Python 3.13, Flask (the only third-party dependency)
- SQLite as a cache; a git-tracked **corpus** of markdown + TSVs is the committed
  state
- Package manager: `uv` (run scripts with `uv run <script>.py`)

## Project Structure

- `*.py` — the engine scripts (see Key Scripts below)
- `app.py` + `templates/` + `static/` — the Flask app
- `hierarchy.py` — the curriculum-hierarchy markdown parser (this repo owns it);
  `FORMAT.md` — the on-disk format spec
- `schema.sql` — canonical schema; `db.db` — live working copy (gitignored);
  `courses/` — the default corpus directory (shipped empty)
- `examples/` — a corpus with one synthetic course, `examples/widgets/`:
  `widgets-ced.md` (reference hierarchy markdown), `plan.md` (the outline +
  course wiring), `objectives.tsv`, `coverage.tsv`
- `plans/` — implementation plans (design record). Do **not** read `plans/done/`
  unless explicitly asked — those describe the code as it was when written.

## Key Scripts

| Script                 | Purpose                                                                                                                                                                  |
|------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `hierarchy.py`         | The curriculum-hierarchy **markdown parser** (this repo owns it): front-matter parsing, flavor detection, `LEVEL_TAGS`/`FLAVOR_KIND`, and `to_nodes` (markdown → flat, already-tagged node-list dict). Used by `load_nodes` (references) and `plan_io` (helpers). |
| `load_nodes.py`        | Parses a hierarchy **markdown** file (`hierarchy.to_nodes`) into the `nodes` table (one uniform, hierarchy-scoped table), registers the hierarchy and upserts its `course`. Overridable course/kind/slug; holds `FLAVOR_META` policy. `load_into` does it on a caller's connection. |
| `plan_io.py`           | Read/write a course as a directory: `read_course` (references + `plan.md` outline + the two TSVs → db) and `write_course` (db → `plan.md` + `objectives.tsv` / `coverage.tsv`; reference markdown left untouched). Objective identity via abbreviated uuid tokens. |
| `import_objectives.py` | Imports raw objectives into a course's pool, interning by text (idempotent). Plain-text (pool only) or TSV (`objective`/`text`, optional `node_id`/`ek` coverage edge, optional `uuid`). `--replace` re-seeds. |
| `seed.py`              | Corpus loader: load each course directory in a corpus (`plan_io.read_course`). `seed` skips courses already present (create-if-absent; run on startup); `load_corpus`/`--all` reloads all. CLI: `uv run seed.py <corpus> [db]`. |
| `rebuild_db.py`        | One-command rebuild from scratch: delete db, apply `schema.sql`, load every course in the corpus. `--corpus <dir>` (default `courses`). |
| `render_outline.py`    | Renders a course's lesson plan to markdown: ordered lessons with objectives, a traceability appendix (every leaf → covering lesson(s)), and a gap list. The deliverable (a report, distinct from the round-trippable `plan.md`). |
| `course_bundle.py`     | Export/import a whole course as one self-contained JSON bundle (course + hierarchies + nodes/attrs + objectives + coverage + targets). `export`/`import` subcommands; also wired into the app (per-course Setup export, sidebar import). Additive to the markdown corpus. |

## Running

```bash
# Rebuild a db from a markdown corpus (a dir of course directories), then render.
uv run rebuild_db.py --corpus <corpus>         # deletes db.db; default corpus 'courses'
uv run render_outline.py db.db plan.md --course <course>

# Load a single hierarchy markdown file into a db (the step rebuild_db orchestrates).
uv run load_nodes.py <your-hierarchy>.md db.db --course <course>

# Export a course back to its corpus directory (plan.md + the two TSVs).
uv run python -c "import plan_io; plan_io.write_course('db.db', '<course>', '<corpus>/<course>')"

# Web app (port 5001): bootstraps an empty db from schema.sql, then loads the
# corpus dir. Setup is sidebar-driven: the top "+" creates (or imports) a course;
# each course's controls live on its sidebar block -- "+" uploads a hierarchy
# markdown, the title is click-to-edit, per-reference star/trash set-primary/delete,
# and the course header has bundle-export + delete; the Settings page does global
# restore-from-corpus.
uv run app.py
LESSON_CORPUS_DIR=examples uv run app.py       # load the bundled widgets example
```

The `examples/` corpus (`examples/widgets/`) is a drop-in example course — see
`README.md`.

## Markdown

When writing markdown include blank lines before and after lists and between list
items.
