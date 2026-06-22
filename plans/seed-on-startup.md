# Plan: Seed a blank database from a configured directory on startup

## Goal

Point the app at a **seed directory** of input files (hierarchy node-list JSON +
objectives files) and have it populate a blank database automatically on startup —
creating courses, loading their hierarchies, and importing raw objectives (both
**categorized** and **pool-only**) — with **no clicking through the UI**.

This is for getting a fresh server (or a teammate's checkout) to a useful state in
one step, the way `rebuild_db.py` rebuilds from committed snapshots — but from the
*source* inputs (the extractor's JSON + hand-authored objectives) rather than the
`export/` TSV state.

## The crux: what the input files DON'T tell us

The files we want to drop in carry almost everything except the **app policy** that
ties them to a course. Spelling this out is the heart of the feature.

**Hierarchy node-list JSON** (from hierarchy-extractors' `build_hierarchy_json.py`)
contains `version`, `flavor`, `levels`, `nodes`. By the cross-repo contract
(`json-format.md`) it deliberately carries **only format data** — it does *not*
carry the course it belongs to, the hierarchy kind/slug, or a display title. Those
are consumer policy. So to load one automatically we must supply:

| Needed                | In the JSON? | Default if omitted            | Why it's needed                                              |
|-----------------------|--------------|-------------------------------|-------------------------------------------------------------|
| **course id**         | no           | —  (**required**)             | which `/<course>` this hierarchy belongs to                 |
| course title          | no           | `id.upper()`                  | the course's display name (set once per course)             |
| kind                  | no (flavor)  | derived from `flavor`         | `ced` / `ib-syllabus` / `book`; only needed to override     |
| slug                  | no           | `<course>-<kind>`             | only needed when a course has two hierarchies of one kind   |
| hierarchy title       | no           | derived (e.g. `CSA CED`)      | only to override the derived title                          |

**Objectives files** (plain text, or TSV with an `objective`/`text` column, optional
`uuid`, optional `node_id`) carry the objective text and — if categorized — node ids,
but not the course or which hierarchy those node ids index. So we must supply:

| Needed                  | In the file? | Default if omitted   | Why it's needed                                               |
|-------------------------|--------------|----------------------|--------------------------------------------------------------|
| **course id**           | no           | — (**required**)     | whose pool these objectives join                             |
| **target hierarchy**    | no           | none → **pool-only** | which hierarchy a `node_id` column categorizes into          |

Key point about the target: a TSV can contain `node_id`s, but **which hierarchy
those ids belong to is not in the file** (the same id string could exist in the CED
and a book). Today the UI resolves this by *where you upload* (the hierarchy page).
For unattended loading we must state the target explicitly; omitting it means
"pool only — ignore any node_id column" (matching the Objectives-page upload).

## Design

### A manifest, not magic

Rather than encode course/kind/target in filenames or edit the generated JSON
(which would violate the node-list contract — it's format-only by design), put the
policy in one **manifest** file in the seed directory. Recommended format: **TOML**
(hand-authorable, supports comments, and `tomllib` is in the 3.13 stdlib — no new
dependency). The manifest is the single source of "what to load and as what."

`seed/manifest.toml`:

```toml
# Loaded on startup into a blank database. Paths are relative to this file.

[[course]]
id = "csa"
title = "AP Computer Science A"

  # Hierarchies load first (objectives' coverage references their nodes).
  [[course.hierarchy]]
  file = "csa-ced.json"        # node-list JSON; flavor read from the file
  # kind/slug/title omitted -> kind from flavor, slug "csa-ced", title derived

  [[course.hierarchy]]
  file = "csa-book.json"
  kind = "book"               # disambiguates; slug defaults to "csa-book"

  # Objectives: no `hierarchy` -> pool only (any node_id column ignored).
  [[course.objectives]]
  file = "csa-objectives.txt"

  # Objectives: `hierarchy` set -> categorize node_ids into that hierarchy.
  [[course.objectives]]
  file = "csa-ced-coverage.tsv"
  hierarchy = "csa-ced"       # the slug the node_id column indexes

[[course]]
id = "widgets"
title = "Intro to Widgets"

  [[course.hierarchy]]
  file = "../examples/widgets-hierarchy.json"

  [[course.objectives]]
  file = "../examples/objectives.tsv"   # pool-only (node_id col ignored, no target)
```

This manifest *is* the "extra metadata" the feature needs. Nothing has to be added
to the JSON or objectives files themselves — they stay exactly as the extractor /
the author produce them.

### Configuration

- **`LESSON_SEED_DIR`** env var → the seed directory (contains `manifest.toml` and
  the input files). Unset → seeding is off (current behavior).
- Read at startup. `serve.sh` can export it for the detached/startup server; the
  `.yolorc` startup path then seeds automatically.

### When it runs

At startup, after `ensure_schema()`. For each `[[course]]` in the manifest:

- if the course **already exists**, skip it (log "skipping, exists") — so restarts
  are safe and re-running never duplicates or errors;
- otherwise create it (id + title, eager outline like `course_new`) and load its
  hierarchies, then its objectives.

This makes seeding **create-if-absent per course**. (Simple and safe; the documented
limitation is that it won't retro-add a hierarchy to a course that already exists —
edit via the UI, or clear the db to re-seed. See "Decisions".)

### The loader

A new module `seed.py` with `seed(db_path, seed_dir)` that reads the manifest and
calls the **existing library functions** — no HTTP, no reimplementation:

1. **Course**: `INSERT INTO courses`, then `ensure_outline` (mirrors `course_new`).
2. **Hierarchy** (per `course.hierarchy`), in manifest order:
   - `doc = load_nodes.load_doc(json.load(file))`
   - `kind = kind or load_nodes.meta_for(doc["flavor"])["kind"]`,
     `slug = slug or f"{course}-{kind}"`
   - `rows = load_nodes.build_rows(slug, doc["nodes"])`
   - `load_nodes.load(db, slug, course, kind, course_title, rows, source=file, title=title)`
   - link the outline to it in `hierarchy_targets` (same as the upload route).
3. **Objectives** (per `course.objectives`), after all hierarchies:
   - `items, mode = import_objectives.parse_items(file)`
   - if `hierarchy` given: validate node ids against that hierarchy and
     `import_objectives.load(db, course, items, hierarchy=slug)` (drop+log unknown
     ids, exactly like `hierarchy_upload`);
   - else: strip node ids and `import_objectives.load(db, course, items)` (pool-only,
     like `objectives_upload`).

Order is enforced by the loader (course → hierarchies → objectives), independent of
how the manifest is written, so categorized coverage always finds its nodes.

Wire-in (in `app.py`, after `ensure_schema()`):

```python
seed_dir = os.environ.get("LESSON_SEED_DIR")
if seed_dir:
    seed.seed(DB_PATH, seed_dir)   # logs a per-course summary; never fatal
```

### Observability & errors

- Print a per-course / per-file summary to stdout (lands in the serve log): what was
  created, counts loaded, what was skipped.
- Non-fatal: a missing file, a bad JSON, or an unknown-id set logs a warning and
  continues — a broken seed file must not stop the server from booting.

### CLI (free, same module)

Expose `seed.py` as a command too, for manual/initial population without the app:

```bash
uv run seed.py <seed-dir> [db.db]
```

Complements `rebuild_db.py` (which rebuilds from `export/` snapshots): `seed.py`
populates from source inputs via the manifest.

## Reuse summary

No new ingest logic — the feature is a thin orchestrator over `load_nodes`
(`load_doc`/`meta_for`/`build_rows`/`load`), `import_objectives` (`parse_items`/
`load`), and the `ensure_outline` + `hierarchy_targets` linking the routes already
do. The only new artifact is the manifest schema.

## Decisions to confirm

- **Manifest format**: TOML (recommended — comments, stdlib `tomllib`, hand-friendly)
  vs JSON (consistent with the rest of the repo, but no comments).
- **Re-seed policy**: create-if-absent per course (recommended) vs only-when-db-empty
  vs a deeper merge that adds missing hierarchies/objectives to existing courses.
- **Manifest vs filename conventions**: a manifest is explicit and handles multiple
  hierarchies + categorization targets; filename conventions (`csa.ced.json`,
  `csa.coverage.csa-ced.tsv`) avoid a manifest but are fragile and can't express
  titles/overrides. Recommend the manifest.
- **Default seed dir**: none (opt-in via env) vs a conventional `./seed` auto-used
  when present.

## Out of scope / possible extensions

- **Auto-import course bundles**: let the seed dir also contain `*.course.json`
  bundles (from the existing export) and import any whose course is absent — a
  zero-metadata path (the bundle already carries everything). Complementary to the
  manifest; could land later.
- **Reload on change / hot seeding** while running (this plan only seeds at startup).
- **`hierarchy_targets` control** beyond the default "outline measured against every
  reference" (tracked separately).
- Making the manifest the committed source of truth in place of `export/` TSVs.
