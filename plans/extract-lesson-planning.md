# Plan: Extract the lesson-planning **tool** into its own repo (with history)

## Goal

Pull the lesson-planning system out of `bhs-awesome` into a standalone git
repository that **preserves the development history** of every file it carries.

Scope decision (set by the user): the new project is the **generic tool only**.
It carries **no course data** and **no course-specific scripts** (the PDF
scrapers / hierarchy extractors). The result is a course-agnostic lesson-planning
app you point at *your* hierarchies and objectives — none baked in. CSA/CSP/IB
content stays in `bhs-awesome`.

This has two parts: (1) a history-preserving **extraction** of the engine, and
(2) a small amount of **genericization** to remove the now-dangling references to
the course files we're leaving behind.

## TL;DR — the approach

**Work in a fresh local clone and rewrite it with `git filter-repo`.** Don't run
the rewrite against the original working repo, and don't hand-copy files (that
loses history). Concretely:

1. From the new directory, clone the source over the filesystem:
   `git clone /path/to/bhs-awesome lesson-planning`.
2. Run `git filter-repo` with an explicit keep-list of the **engine** files only
   (app + the scripts it imports + the design plans). This drops every other
   path — including all data and all course-specific scripts — and prunes
   commits that no longer touch anything, while keeping each surviving commit's
   original author, date, and message.
3. **Genericize** the few places that hard-code the CSA/CSP/IB file set
   (`rebuild_db.py` defaults and the app's `/data` route), add standalone
   scaffolding (trimmed `pyproject.toml`, `.gitignore`, `README`, rewritten
   `CLAUDE.md`), verify the empty app boots and accepts a user-supplied
   hierarchy, then point the clone at a new GitHub remote.

This beats "a new directory with read access to the repo": read access is enough
to *clone*, and `filter-repo` needs a real git repo to rewrite — the clone **is**
the new project, with no copy step.

Why `filter-repo` (not `subtree split` / `filter-branch`): our keep-set is
scattered across the repo root, `lesson-planning/`, and `plans/`, so `subtree`
(single-directory only) can't express it; `filter-branch` is deprecated and slow.
`filter-repo` takes a path keep-list, unions it, and prunes emptied commits.

Install if missing: `pip install git-filter-repo` (or `brew install
git-filter-repo`). It refuses to run unless the repo is a fresh clone; honor that
— only ever rewrite the throwaway clone.

## What the tool actually needs

Paths are relative to the `bhs-awesome` root. The executor should re-derive this
closure from the code rather than trust the list blindly, but it is correct as of
this writing — and note that, with the course content gone, **every kept file is
generic** (format-aware, not data).

### Keep — the engine

App package (already self-contained under one dir):

- `lesson-planning/app.py`
- `lesson-planning/templates/` (all)
- `lesson-planning/static/` (all — favicons, `INSTALL.md`)
- `lesson-planning/schema.sql`

Root scripts the app imports, transitively
(`app.py` → these; `rebuild_db` → `import_planning`, `load_nodes`; everything →
`hierarchy`). None is course-specific — they're the generic engine:

- `hierarchy.py` — markdown-hierarchy parser. Knows the *shapes* of CSA/CSP/IB/
  book markdown (its `LEVEL_TAGS` / flavor auto-detection), which is input-format
  knowledge, **not** course data. Keep as-is **for now** — but it's a copy shared
  with the extractors repo, slated for removal once the data-driven boundary
  lands; see "`hierarchy.py`: the consumer side of a data-driven boundary" below.
- `load_nodes.py`
- `import_objectives.py`
- `export_planning.py`
- `import_planning.py`
- `rebuild_db.py` (engine kept; its baked-in course list is stripped — see
  "Genericize")
- `render_outline.py`

### Keep — the design history (plans)

- `plans/lesson-planning.md`
- `plans/scripts-into-app.md`
- `plans/unified-hierarchies.md`
- `plans/done/elevate-course-outline.md`

These are prose design docs for the tool. Some *mention* CSA/BHSawesome as the
worked example; that's fine (it's design rationale, not shipped data).
`plans/categorize-objectives.md` is **excluded** — it's the most CSA-specific
plan, and the new repo's `plans/` should read as course-neutral.

### Explicitly excluded — data

No course data travels, in the working tree **or** anywhere in history (because
`filter-repo` removes excluded paths from every commit they appear in):

- The committed planning snapshots: `lesson-planning/export/*.tsv` (these are
  CSA/CSP/IB objectives, coverage, nodes, hierarchies — i.e. data). The app
  recreates `export/` at runtime; ship it empty (a `.gitkeep`).
- All hierarchy markdown and objective inputs: `csa/`, `csp/`, `ib/` in their
  entirety (`*-hierarchy.md`, `bhsawesome-outline.md`, `learning-objectives/`,
  PDFs, scans, `ced.xml`/`ced.html`, quizzes, …).
- The live `db.db` (already gitignored; regenerated from `schema.sql`).

### Explicitly excluded — course-specific & unrelated scripts

- **Course/PDF scrapers and hierarchy extractors** (the user's exclusion):
  `extract_ib_hierarchy.py`, `extract_ib_hours.py`, `extract_book_hierarchy.py`,
  `extract_book_text.py`. These scrape specific CEDs/guides/books — they belong
  with the data in `bhs-awesome`. Their output (the hierarchy markdown) is data a
  user of the generic tool supplies themselves.
- **CED-HTML / quiz / deck / activity tooling** (unrelated, even where it shares
  `hierarchy.py`): `build_hierarchy_xml.py`, `build_hierarchy_db.py`,
  `ced-to-html.xsl`, `Makefile`, `format_xml.py`, `extract_key.py`,
  `extract_activities.py`, `activity_report.py`, `compare_activities.py`,
  `filter_pairs.py`, `check_deck.py`, `rename_card_tags.py`, `list_files.py`,
  `identify.py`, `lcs.py`, `jaccard.py`, `load_objectives.py` (superseded by
  `import_objectives.py`), `just-pretext.sh`, `google-java-format.jar`,
  `.xml-formats/`, `decks/`, `reports/`, `workflows/`.
- Their design plans stay behind too (`summarize-*`, `compare-books*`,
  `extract-mcqs`, `extend-format-xml`, `genalize-build-ced-xml`).

Net effect on dependencies: with the `extract_*`/`build_*` scripts gone, **no
kept file imports `lxml` or `pypdf`** (those were only used by the scrapers). The
generic tool's only third-party dependency is **Flask**.

## Two phases

The end state is a flat repo (app at the root, no `lesson-planning/` subdir). But
we get there in two steps so each is small and reviewable:

- **Phase 1** — extract with history + genericize, keeping the **current shape**
  (app under `lesson-planning/`, scripts at root). This is a pure subset of the
  source plus targeted edits, so the rewrite needs *zero* path changes and we can
  confirm the tool works before moving anything.
- **Phase 2** — flatten the app up to the repo root, as ordinary `git mv` + edit
  commits in the new repo (its own clean history, easy to review or revert).

`app.py` computes `REPO_ROOT = dirname(dirname(__file__))` (the parent of
`lesson-planning/`) and adds it to `sys.path` to import the root scripts — which
is exactly the coupling Phase 2 unwinds. Phase-1 shape:

```
lesson-planning-repo/                # Phase 1
  hierarchy.py  load_nodes.py  import_objectives.py  export_planning.py
  import_planning.py  rebuild_db.py  render_outline.py
  lesson-planning/            # the Flask app (export/ shipped empty)
  examples/  plans/
  pyproject.toml  .gitignore  README.md  CLAUDE.md
```

## Phase 1 — extract and get it working (current shape)

### 1. Prep the source

Ensure the lesson-planning work is committed (and pushed, so a clone sees it).
The current working tree has unrelated untracked dirs (`bhsawesome.OLD/`,
`misc/`) and modified `export/*.tsv`; a clean clone of `HEAD` avoids them.

### 2. Fresh clone

```bash
git clone /Users/peter/hacks/bhs-awesome lesson-planning
cd lesson-planning
```

### 3. Write the keep-list and rewrite

```bash
cat > /tmp/keep.txt <<'EOF'
# --- app (export/ excluded; it holds course data) ---
lesson-planning/app.py
lesson-planning/templates/
lesson-planning/static/
lesson-planning/schema.sql
# --- root scripts (the engine: app import closure) ---
hierarchy.py
load_nodes.py
import_objectives.py
export_planning.py
import_planning.py
rebuild_db.py
render_outline.py
# --- design plans (categorize-objectives.md excluded: too CSA-specific) ---
plans/lesson-planning.md
plans/scripts-into-app.md
plans/unified-hierarchies.md
plans/done/elevate-course-outline.md
EOF

git filter-repo --paths-from-file /tmp/keep.txt --prune-empty always
```

Notes for the executor:

- We list the app's sub-paths individually (not `lesson-planning/`) so that
  `lesson-planning/export/*.tsv` is **excluded** — keeping a whole subtree would
  drag the data snapshots in. Verify the exact flag/format against the installed
  `filter-repo` version; equivalently use repeated `--path …` args.
- `--prune-empty always` drops commits that, after filtering, touch nothing
  (e.g. a commit that only edited an export TSV or a scraper).
- `filter-repo` removes the `origin` remote on purpose. Good — add a new one in
  step 7.
- Because excluded paths are stripped from *every* commit, no course data and no
  scraper code survives anywhere in the new repo's history — not just its tip.

### 4. Verify history survived

```bash
git log --oneline | wc -l                       # fewer than original, still non-trivial
git log --oneline -- lesson-planning/app.py      # full design history of the app
git log --oneline -- hierarchy.py                # shared-parser history intact
git log --oneline -- render_outline.py
```

Each kept file should show its real commits with original dates/authors, not a
single squashed "import" commit. Spot-check that no `csa/`/`csp/`/`ib/` path and
no `extract_*`/`build_*` file appears anywhere:
`git log --all --oneline -- csa/ ib/ csp/ 'extract_*.py'` should be empty.

### 5. Genericize — remove the baked-in course set

This is the work that makes the tool truly course-independent (not merely
data-free). After extraction, these spots still name the CSA/CSP/IB files we
dropped:

- **`rebuild_db.py` — `DEFAULT_HIERARCHIES`.** It hard-codes
  `csa/ced-2025-hierarchy.md`, `csp/ced-hierarchy.md`, `ib/ib-hierarchy.md`, and
  the `csa/bhsawesome-outline.md` override. Remove the baked-in list: make
  `rebuild_db` require explicit hierarchy-file argument(s) (it already accepts
  positional `hierarchy` args and only falls back to `DEFAULT_HIERARCHIES`). Set
  the default to empty and update the `--help`/usage text and the module
  docstring's example accordingly.
- **`lesson-planning/app.py` — the `/data` "restore snapshot" route.** It builds
  `specs` from `rebuild_db.DEFAULT_HIERARCHIES` and joins them under `REPO_ROOT`
  to bulk-load the known courses. With no defaults, that button loads nothing.
  The app *already* has the generic path — the `load_reference` upload route
  (upload a hierarchy markdown → `load_nodes`) — so the genericization is:
  drop/replace the "load the known course files" affordance and lean on
  upload-your-own + restore-from-export. Update `templates/data.html` to match.
  (A fuller `/data` redesign is a follow-up, not part of this extraction; the
  minimum here is *no broken references to deleted files*.)
- **Grep for stragglers** before declaring done:
  `grep -rnE 'csa/|csp/|ib/|bhsawesome|learning-objectives|ced-2025' . --include='*.py' --include='*.html'`
  — every hit should be gone or clearly generic example text.

### 6. Make it a standalone project

- **`pyproject.toml`**: rename the project to `lesson-planning`; trim
  `dependencies` to **`flask` only** (drop `lxml` and `pypdf` — no kept file
  imports them once the scrapers are gone). Keep `requires-python = ">=3.13"` and
  `.python-version`. Regenerate the lock: `uv lock` (don't carry the old
  `uv.lock`).
- **`.gitignore`**: reduce to what applies — `*.db`, `*~`, `__pycache__`,
  `.venv/`, `.claude/settings.local.json`, `db-backups/`. Drop the
  book/PDF/HTML/comparison ignores.
- **`CLAUDE.md`**: rewrite small. Describe only the engine scripts, the
  rebuild/seed pipeline, and the app — as a course-agnostic tool (no CSA/CSP/IB
  artifact inventory). Lift the lesson-planning rows of the old Key Scripts table
  and the seed/rebuild/run commands, with the course-specific paths replaced by
  `<your-hierarchy>.md` placeholders.
- **`README.md`**: new — what it is, how to seed from your own hierarchy
  (`uv run load_nodes.py <your-hierarchy>.md lesson-planning/db.db`) and run
  (`uv run lesson-planning/app.py`, port 5001).
- **`lesson-planning/export/.gitkeep`**: add it (the dir ships empty; the app
  writes snapshots here).
- **Synthetic example fixture (included).** Since there's no data to test
  against, author a small *synthetic* hierarchy under `examples/` — a handful of
  made-up nodes in a clearly-fictional course (e.g. an "Intro to Widgets" course
  with a couple of units and a few learning objectives), not a real course. It
  serves three purposes: documents the input markdown format the tool expects, is
  the fixture the step-7 smoke test loads, and gives the `README` a concrete
  worked example. Keep it minimal but exercise each hierarchy level the parser
  recognizes so it doubles as a format reference. Write the matching objectives
  TSV (`examples/objectives.tsv`) too, so `import_objectives.py` has something to
  ingest.

### 7. Smoke-test the extracted tool (empty-start)

```bash
uv run lesson-planning/app.py          # boots a fresh db from schema.sql on an empty repo
# In the UI: Data page -> upload examples/<fixture>.md -> it loads via load_nodes.
# Then import the example objectives and render the outline:
uv run import_objectives.py examples/objectives.tsv lesson-planning/db.db
uv run render_outline.py lesson-planning/db.db /tmp/plan.md --course <fixture-course>
```

The success criterion is that the app **starts with no data**, accepts a
user-supplied hierarchy through the Data page, and produces an outline — with no
reference anywhere to the dropped course files.

### 8. Publish

```bash
gh repo create <name> --private --source=. --remote=origin
git push -u origin main
```

(Per the user's global workflow notes, the user pushes; in a sandboxed/yolo
container you likely lack push access — stop at the verified local repo and hand
off.)

Phase 1 done-criterion: a published (or hand-off-ready) repo where the app boots
empty, ingests the `examples/` fixture, and renders an outline — in the current
shape, with full per-file history.

## Phase 2 — flatten the app to the repo root

Do this only after Phase 1 is verified. It's pure reorganization (no behavior
change), as normal commits in the new repo. Target:

```
lesson-planning-repo/                # Phase 2
  app.py  templates/  static/  schema.sql  export/   # app, promoted to root
  hierarchy.py  load_nodes.py  ... render_outline.py # scripts (already at root)
  examples/  plans/  pyproject.toml  .gitignore  README.md  CLAUDE.md
```

Steps:

1. Move the app package up with history-preserving renames:
   `git mv lesson-planning/app.py app.py`, and likewise `templates/`, `static/`,
   `schema.sql`, `export/`. Then remove the now-empty `lesson-planning/` dir.
2. Fix the path coupling in `app.py`:
   - `REPO_ROOT = dirname(dirname(__file__))` → `dirname(__file__)` (app.py now
     sits at the root). The `sys.path` insert can stay (harmless) or be dropped —
     once `app.py` and the scripts share a directory, sibling imports resolve
     without it.
   - `EXPORT_DIR`, `SCHEMA_PATH`, and the default `DB_PATH` are already
     `dirname(__file__)`-relative, so they follow `app.py` to the root with no
     change — just make sure `export/` and `schema.sql` moved alongside it.
   - Flask's default template/static lookup is relative to the app module's dir,
     so moving `templates/` and `static/` next to `app.py` keeps them found with
     no config change.
3. Update the run command everywhere it appears (`uv run lesson-planning/app.py`
   → `uv run app.py`): `README.md`, `CLAUDE.md`, and any inline docstrings.
4. Re-run the step-7 smoke test to confirm the flattened layout still boots
   empty, loads the fixture, and renders an outline.

Resolved by the user: drop `categorize-objectives.md`; include the synthetic
`examples/` fixture; flatten — but in two phases (work in the current shape
first, reorganize second). All reflected above.

## `hierarchy.py`: the consumer side of a data-driven boundary

This repo keeps `hierarchy.py` because `load_nodes.py` imports it today —
`parse_sections`, `LEVEL_TAGS[flavor][level]` (to store the level's tag name in
`nodes.level`), and `hierarchy_title()`. That makes `hierarchy.py` a copy
**shared** with the extractors repo (`plans/extract-extractors.md`), which *owns*
the file — the markdown format is defined by what the extractors emit.

The agreed end state (decided in the extractors plan) is to share **data, not
code**: the extractor library emits each hierarchy as an already-parsed,
already-tagged JSON node-list, e.g.

```json
{ "flavor": "csa",
  "nodes": [
    {"id": "1.1.A.1", "level": 4, "tag": "essential-knowledge", "parent": "1.1.A", "text": "..."}
  ] }
```

Because every node already carries its `tag`, the consumer needs no flavor
detection and no markdown parsing — just the generic tree shape `load_nodes`
already builds (`parent_id`, `is_leaf`, `ordinal`, `text`). `FLAVOR_META`
(flavor → course/kind/slug) is already local app policy and stays;
`hierarchy_title()` is a trivial `f"{course.upper()} …"` helper that needs only
`course`/`kind` (both already local) and folds inline.

### Scope: now vs. follow-up

- **Now (this extraction):** keep `hierarchy.py` and `load_nodes.py` exactly as
  they are — the app must keep loading hierarchies. Do **not** block this
  extraction on the JSON work.
- **Follow-up (cross-repo — the *same* task tracked in the extractors plan):**
  the extractor library gains a `markdown → nodes JSON` emit step; rewrite
  `load_nodes.py` here to load that JSON instead of calling
  `parse_sections`/`LEVEL_TAGS`, fold `hierarchy_title()` inline, and then
  **delete this repo's `hierarchy.py` copy**. After that the two repos share only
  a documented JSON schema, no code.

**Tradeoff:** the app currently ingests hand-editable hierarchy markdown directly
(the `/data` upload route → `load_nodes`). Under the JSON contract, a hand-edited
hierarchy must first pass through the extractor library's parse→emit step. Decide
at follow-up time whether this repo keeps a minimal markdown reader for that
convenience or relies on the emit CLI — the extractors plan ships that emit step
as an easy CLI for exactly this reason.

## Decision left for publish time

**Repo name / visibility on GitHub** — the slug (e.g. `peter/lesson-planning`)
and public vs. private passed to `gh repo create` in step 8, plus whether the
user or the executor runs it. Nothing to settle until then.

## Loose ends to handle during execution

- `lesson-planning/cb-favicon.zip` and `db.db` are **untracked** today, so
  `filter-repo` won't carry them. `db.db` is regenerated — leave it out. If the
  favicon zip matters, copy + commit it in the new repo.
- The current working tree shows modified `export/*.tsv` — irrelevant now, since
  `export/` is excluded entirely.
- After extraction, decide what to do in the **old** repo (leave lesson-planning
  in place, or remove it and link to the new repo). Separate cleanup, out of
  scope here.
```