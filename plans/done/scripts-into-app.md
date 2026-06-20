# Bring the lesson-planning scripts into the app

Make the web app self-sufficient: every operation you'd otherwise run from the
command line — load a reference hierarchy, seed objectives, render the
deliverable, snapshot/restore the database — is doable from the UI. The CLI
scripts stay runnable (they're how `rebuild_db` and CI work), but day-to-day a
teacher should never have to drop to a terminal to add a course or get their
plan out.

## The key fact this plan rests on

Every script already splits into a thin `main()` (argparse + `print`) over an
importable **library function** that does the real work against a db path:

| Script | Library entry point | In the app today? |
|---|---|---|
| `import_objectives.py` | `parse_text(content)`, `load(db, course, items, hierarchy=, replace=)` | **Yes** — `objectives_upload`, `hierarchy_upload` |
| `export_planning.py` | `export(db, out_dir)` | **Yes** — `/<course>/export` + "Export snapshot" button |
| `load_nodes.py` | `parse_sections`, `build_rows(slug, flavor, sections)`, `meta_for(...)`, `load(db, slug, course, kind, title, rows, source=)` | No — CLI only |
| `render_outline.py` | `fetch(conn, course)`, `render(course, *data)` | No — CLI only |
| `import_planning.py` | `load(db, in_dir)` | No — CLI only |
| `rebuild_db.py` | `rebuild(db, schema, export_dir, specs)` | No — CLI only |

`app.py` already does `sys.path.insert(...)` to the repo root and imports
`export_planning` and `import_objectives` directly. So incorporating the rest is
**adding routes + templates that call these functions** — there is no business
logic to port or duplicate. That keeps the scripts as the single source of truth
and means the app and CLI can never drift.

This plan is therefore mostly UI surface plus a few correctness wrinkles that
only show up once a human (not a careful CLI invocation) drives these operations.

## What's missing, in priority order

0. **First-run bootstrap** — starting the app with no `db.db` should produce a
   valid, empty database (schema applied), ready to be populated *from the app*.
   Today a first run yields an empty file and a "no courses loaded — run
   load_nodes.py first" dead end (`app.py:485`). This is the foundation the rest
   builds on, so it's Stage 1.
1. **Load a reference hierarchy / add a course** (`load_nodes`) — the headline.
   Today a new course only exists after `uv run load_nodes.py …` at a terminal.
2. **Download the deliverable** (`render_outline`) — the whole point of the tool
   is the rendered plan, and there's no way to get it out of the app.
3. **Restore / import a snapshot** (`import_planning`) — `export` is a button;
   its inverse (load the committed snapshot — to bootstrap a fresh db, or to
   discard live edits) is not.
4. **Rebuild from version control** (`rebuild_db`) — destructive (deletes the db
   file); **stays CLI-only** (decided). Its in-app role is filled by bootstrap +
   restore below, neither of which deletes the file.

## 0. First-run bootstrap (apply `schema.sql`)

Starting the app against a missing or tableless `db.db` should leave a fully
schema'd, empty database — so the app renders cleanly (empty sidebar, a prompt
to add data) instead of a 404 dead end, and the in-app load/import actions below
have tables to write into.

`ensure_schema()` runs at import (`app.py:1246`) but only performs idempotent
*migrations* on an existing db; the loaders' embedded `CREATE TABLE IF NOT
EXISTS` only fire when you actually load something, so a truly fresh start has no
tables. The fix is small: before the migrations, if the canonical tables are
absent, apply the canonical schema.

```python
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

def ensure_schema():
    with db() as conn:
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "courses" not in have:                 # fresh/empty db -> apply canonical schema
            conn.executescript(open(SCHEMA_PATH).read())
    # ... existing idempotent migrations unchanged ...
```

`schema.sql` is already the canonical, all-`IF NOT EXISTS` description of every
table (it says so in its header) — the same file `rebuild_db` applies — so this
reuses the existing source of truth, it doesn't add a second one. The `index`
route's "no courses loaded" message should change from "run load_nodes.py" to
"add a reference or import a snapshot" pointing at the in-app actions.

With this in place, the app's lifecycle becomes: **start (empty) → populate from
the app**, where "populate" is either §1 (load hierarchies + objectives from
files) or §3 (import a committed snapshot) — exactly the two paths requested.

## 1. Load a reference hierarchy (`load_nodes`)

The user-facing shape: an **"Add reference / course"** action that takes a
hierarchy markdown upload and runs `parse_sections → build_rows → load`, exactly
as the CLI does, registering the course + reference and loading the nodes.

A new route, e.g. `POST /hierarchy/new` (global, not course-scoped — it may be
creating the course):

```python
content = f.read().decode("utf-8", "replace")
flavor, sections = load_nodes.parse_sections(content)        # auto-detect
m = load_nodes.meta_for(flavor, course=..., kind=..., slug=..., course_title=...)
rows = load_nodes.build_rows(m["slug"], flavor, sections)
load_nodes.load(DB_PATH, m["slug"], m["course"], m["kind"], m["course_title"],
                rows, source=f.filename)
```

Design points that the CLI gets for free but a UI must handle:

- **Flavor override.** Auto-detection handles CSA/CSP/IB, but some files need
  overrides — `rebuild_db.DEFAULT_HIERARCHIES` maps `csa/bhsawesome-outline.md`
  to `course=csa, slug=csa-book, kind=book` because its *book* flavor would
  otherwise mint a separate `book` course. The form should detect the flavor,
  show the derived `course`/`kind`/`slug`/`title`, and let the user edit them
  before committing (a two-step "upload → preview → confirm", or a single form
  with the detected values pre-filled). `meta_for` already implements exactly
  this override precedence — pass the form fields straight through.

- **Re-loading an existing hierarchy strands coverage.** `load_nodes.load` does
  `DELETE FROM nodes WHERE hierarchy=?` then re-inserts. `coverage` rows pointing
  at node_ids that vanished in the new version are left dangling (runtime FKs are
  off). Before/after the load, compute which `coverage(hierarchy=slug)` node_ids
  are no longer present and **flash a warning with the count and a sample** — the
  same courtesy `objectives_upload`/`hierarchy_upload` already pay for unknown
  ids. (Optionally offer to drop the orphaned edges; default to keeping them so a
  typo in the markdown doesn't silently delete mappings.)

- **This is also "add a course."** `load(...)` upserts `courses` and registers
  the hierarchy; a brand-new course simply appears in the sidebar afterward. So
  there's no separate "new course" flow needed for the common case — see
  Deferred for the empty-course case.

- **Where it lives.** A global **"+ Add reference"** control in the header or at
  the top of the sidebar's course list. This directly replaces the help page's
  "Loading a new reference or course" CLI section (§Help, below).

## 2. Download the deliverable (`render_outline`)

`render_outline.render(course, *render_outline.fetch(conn, course))` returns the
finished markdown string. Mirror the existing `objectives_tsv` download route:

```python
@app.route("/<course>/outline.md")
def outline_md(course):
    with db() as conn:
        data = render_outline.fetch(conn, course)
    md = render_outline.render(course, *data)
    return Response(md, mimetype="text/markdown",
                    headers={"Content-Disposition":
                             f'attachment; filename="{course}-plan.md"'})
```

- Add a **"Download plan (.md)"** link in the course-outline page header, next to
  "Export snapshot".
- Consider also rendering it as an **in-app HTML view** (the traceability table +
  gap list are genuinely useful on-screen, not just as a file). The app already
  has markdown-ish `inline`/`blocks` filters; a read-only `outline.html` page
  that shows the same content would be a small add.
- **Wrinkle:** `render_outline.fetch` sets `conn.row_factory = sqlite3.Row`
  (the app's `db()` already does) and resolves the outline with
  `ORDER BY (kind='lesson-plan') DESC` — but the kind was renamed to
  `'course-outline'` (see `ensure_schema` stage and commit history). The
  `editable=1` filter still selects the right row, so it works, but update the
  tiebreaker to `'course-outline'` for clarity while we're in here. The app's own
  `outline_hierarchy()` helper is the canonical resolver — consider having
  `render_outline.fetch` accept a pre-resolved `(R, O)` or factor the resolver so
  the two can't diverge.

## 3. Restore / import a snapshot (`import_planning`)

`export` is already a button. Its inverse — `import_planning.load(DB_PATH,
EXPORT_DIR)` — loads the committed `.tsv` snapshot. This is the second of the two
requested populate paths and serves double duty:

- **On a fresh (bootstrapped) db** it is the one-click "load all my real data"
  path — non-destructive, because there's nothing to discard.
- **On a live db** it means "discard uncommitted edits and reload the snapshot" —
  destructive, so gate *that* case behind a confirm. (Detect: are there any
  authored rows? If empty, no confirm needed.)

**The ordering catch.** `import_planning` deliberately does *not* load reference
`nodes`/`hierarchies` — those come from markdown via `load_nodes`, and the export
only carries the authored OUTLINE hierarchies (reloaded by a scoped delete). So a
snapshot import **alone** restores objectives/coverage/outlines but leaves the
references missing; outlines would point at absent reference nodes. A full
restore is references-first, then snapshot — which is exactly what `rebuild_db`
does, minus the file delete:

```
schema (bootstrap §0)  →  load_nodes for each reference markdown  →  import_planning
```

So offer a single **"Restore everything from version control"** action that runs
the non-destructive tail of `rebuild` (the `load_nodes` loop over
`rebuild_db.DEFAULT_HIERARCHIES`, then `import_planning.load`) against the
existing (already-schema'd) db — *without* `os.remove`. Best done by factoring
`rebuild_db.rebuild` so the destructive delete is separable from the populate
step, letting the app call the populate step directly and keeping one source of
truth for the markdown-file list + overrides.

This makes §3 the cohesive "load real data" story, not just an admin nicety. It
belongs on the **Data** page (below).

## 4. Rebuild from version control (`rebuild_db`) — CLI-only (decided)

`rebuild_db.rebuild` **deletes `db.db`** before rebuilding, so it discards every
un-exported edit and removes the file out from under the running app. **It stays
CLI-only.** Its useful in-app outcome (a clean db full of real data) is delivered
instead by §0 bootstrap + §3 restore, neither of which deletes anything. The only
shared code to extract is the *populate* half of `rebuild` (see §3), so the app
reuses it without touching the delete.

## A home for the data operations: a "Data" page

The populate/snapshot operations don't belong in a course's content header. Add
one **`/data`** page (or fold into `/help`) collecting: **Add reference/course**
(§1), **Restore from version control** / **Import snapshot** (§3), and **Export
snapshot** (already built). When the db is freshly bootstrapped and empty, this
page is effectively the app's landing/setup screen — the index route should send
an empty db here. The per-course header keeps only the two things you reach for
while authoring: **Download plan** (§2) and **Export snapshot**.

## Cross-cutting

- **No logic duplication.** Every route calls the script's library function. Keep
  each script's `main()` so the CLI and `rebuild_db`/CI keep working. This is the
  established pattern (`export_planning`, `import_objectives`).
- **Error handling.** Wrap `parse_sections`/`parse_text` in `try/except
  ValueError` and `flash` the message, exactly like the two existing upload
  routes. Reject empty/oversized/non-UTF-8 uploads gracefully.
- **Single-user, local.** `app.secret_key` is a dev constant and there's no auth;
  that's fine for a local tool. The only guardrails needed are confirm-dialogs on
  the *destructive* operations (reload snapshot, rebuild), not access control.
- **Update `help.html`.** The "Loading a new reference or course" and "Saving &
  version control" sections currently instruct the user to run `load_nodes.py` /
  `rebuild_db.py` at a terminal. Once 1–2 land, rewrite them to point at the
  in-app actions (keeping the CLI as the "or, from a terminal" alternative).
- **Update `CLAUDE.md`.** The script table's descriptions should note which
  operations are now also available in the app.

## Staged implementation

- **Stage 1 — bootstrap + the populate paths.** Apply `schema.sql` on a fresh db
  (§0); the **Data** page with "Add reference/course" (§1, with flavor override +
  dangling-coverage warning) and "Restore from version control" / "Import
  snapshot" (§3, reusing the factored populate half of `rebuild`). Together these
  make "start the app → load all real data from the app" work end to end — the
  core of this request. Update `help.html` and the index "no courses" message.
- **Stage 2 — the deliverable out.** `render_outline` download link in the
  course-outline header (§2); fix the stale `kind` tiebreaker.
- **Stage 3 — polish.** In-app HTML view of the rendered plan; optional
  drop-orphaned-coverage on re-load; confirm-gating snapshot import on a
  non-empty db; manual empty-course creation if wanted.

## Open / deferred

- **Empty course, no reference.** Today a course is born from its first reference
  upload (or `import_objectives`). A truly empty course (author the outline
  first, attach a reference later) would need a tiny "new course" form inserting
  into `courses`. Cheap, but no demonstrated need — defer.
- **Editing/deleting a reference in-app.** This plan only *adds* references.
  Renaming a course/hierarchy title, or removing a reference and its coverage, is
  a separate (and more dangerous) capability — out of scope.
- **`rebuild_db` in-app** — decided: CLI-only (§4). Bootstrap + restore cover the
  non-destructive cases.
- **Factoring `rebuild_db.rebuild`** so its populate half (load_nodes loop +
  import_planning) is callable without the `os.remove` — needed by §3's "Restore
  from version control" and keeps the markdown-file list in one place.
- **Sharing the outline resolver** between `app.outline_hierarchy` and
  `render_outline.fetch` so the `kind` tiebreaker can't drift again.
