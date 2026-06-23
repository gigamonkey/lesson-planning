# Plan: Drive course setup from the sidebar (retire the Data page as the hub)

## Goal

Make populating the app a natural, course-first workflow driven from the
**sidebar**, instead of routing everything through the single global **Data**
page. The user should be able to: create a course, add one or more hierarchies to
it, seed its raw objectives, and import objective→node categorizations against a
chosen hierarchy — each from the place in the UI where that thing lives.

This is mostly a UI/UX reorganization. The underlying engine (`load_nodes`,
`import_objectives`, the schema) already does the work; much of this plan is
re-wiring existing library calls to better-placed affordances, plus some new
capability: standalone course creation, a per-course **Setup** page for
management, choosing the coverage-target hierarchy on import, and (the longer-term
aim) **export/import a whole course as a single file**.

## Decisions (resolved)

- **Create-course**: an **inline form** under a `+` next to the sidebar title.

- **Add-hierarchy + management**: a per-course **Setup page** (`/<course>/setup`),
  reached from a **⚙ gear** on each course in the sidebar. It's the course's admin
  surface: list/add/delete hierarchies, export the course, delete/rename the
  course.

- **Objective upload**: a **single control** with a **target-hierarchy dropdown**
  (default = primary reference).

- **Data page**: **slimmed** to the genuinely global operations (restore from
  version control + export snapshot). The TSV `export/` snapshot model stays as-is
  for now; course bundles (below) are **additive**, not a replacement yet.

- **Scope this round**: (a) **delete a hierarchy** and (b) **delete/rename a
  course** are in. (c) the outline-vs-reference targets toggle
  (`hierarchy_targets`) is deferred.

- **North star — export/import a course.** A course becomes a self-contained,
  portable **bundle file**: export writes one file that fully recreates the
  course; import recreates it from scratch. This folds into the sidebar `+`
  (**New course** / **Import course**) and the Setup page (**Export course**).
  Additive to the existing `export/` snapshot mechanism for now; whether the
  bundle eventually *becomes* the committed format is a later call.

## Target workflow

1. **Create a new course** — `+` by the "Lesson Planning" title reveals an inline
   form (course id + title). Creates an empty course that appears immediately in
   the sidebar. (The same `+` also offers **Import course** from a bundle file.)

2. **Add a hierarchy** — on the course's **Setup page** (⚙), load a node-list
   JSON, supplying what the JSON lacks (kind; optional slug/title). The course is
   fixed by context. A course can hold several hierarchies.

3. **Upload raw objectives** into the course's pool (Objectives page, exists).

4. **Import objective categorizations** — a 2-/3-column TSV — **against a chosen
   hierarchy** via the target dropdown on the same Objectives upload control.

## Current state (what we're changing)

Grounded in the code as of this plan:

- **Courses are never created on their own.** A `courses` row appears only as a
  side effect of `load_nodes.load()` (hierarchy upload upserts the course) or
  lazily via `ensure_outline()` (first visit to a course's plan page).

- **The Data page (`/data`, `templates/data.html`) is the setup hub**: upload a
  hierarchy JSON → `load_nodes` (`/data/hierarchy/load`, with optional
  course/kind/slug/title overrides); "Restore from version control"
  (`/data/restore` → `rebuild_db.populate`); "Export snapshot"; and a read-only
  table of loaded hierarchies. It's also the empty-db landing (`index()`
  redirects there with no courses).

- **The sidebar** (`inject_nav` + `templates/base.html`) lists each course with
  **Course outline** and **Objectives** links, then its reference hierarchies
  (kind pills). Footer: **Help** / **Data** links + **Export snapshot**. No
  create/add/manage affordances.

- **Objectives upload** (`/<course>/objectives/upload`) runs `import_objectives`
  and **always lands coverage in the primary reference** (`reference_slug`) —
  `import_objectives.load()` takes a `hierarchy=` arg the route never passes.

- **Hierarchy kinds**: references `editable=0` (`ced` / `ib-syllabus` / `book`);
  outline `editable=1` (`course-outline`). `kind` drives the pill and the title
  (`load_nodes.hierarchy_title`).

## Design

### A. Sidebar: create / import a course

Beside `<h1>Lesson Planning</h1>` in `base.html`, a `+` reveals a small inline
panel with two actions:

- **New course** — inline form (course id + title) → **`POST /course/new`**:
  validate the id (non-empty, URL-safe, unique), insert the `courses` row, eagerly
  `ensure_outline()`, redirect to the course. Flash + return on bad/duplicate id.

- **Import course** — file input → **`POST /course/import`**: read a bundle file
  and recreate the course (see §F). (Can land in a later phase; the `+` shows it
  from the start.)

htmx-revealed inline form, matching the app's lightweight style.

### B. Per-course Setup page (the admin surface)

A **⚙ gear** on each course row in the sidebar links to:

- **`GET /<course>/setup`** (`templates/setup.html`) showing:

  - the course's **loaded hierarchies** (the table currently on `/data`, scoped
    to this course) with **delete** (and edit/rename later) per row;

  - an **add-hierarchy form**: a `.json` file input, a **kind** select
    (`ced` / `ib-syllabus` / `book`), and an "overrides" disclosure for slug /
    title;

  - **course-level actions**: **Export course** (§F) and **Delete course** /
    rename.

- **`POST /<course>/hierarchy/load`** — the per-course hierarchy load. Reuse the
  current `/data/hierarchy/load` logic (factor it into one shared helper):
  `load_nodes.load_doc(json)` → `meta_for(flavor, course=<fixed>, kind, slug,
  title)` → `build_rows` → `load`, then redirect to `/<course>/setup`. Keep the
  "orphaned coverage" warning.

  **Slug default fix**: in the course-first flow the slug must default to
  `<course>-<kind>` (e.g. `widgets-ced`), **not** the flavor's built-in slug
  (`meta_for` returns `csa-ced` for a csa-flavored file regardless of course). The
  route computes `slug = override or f"{course}-{kind}"`.

- **`POST /<course>/hierarchy/<hierarchy>/delete`** — delete a hierarchy: its
  `nodes`, `coverage` edges into it, and any `hierarchy_targets` rows referencing
  it. Confirm in the UI; flash a summary.

- **`POST /<course>/delete`** and **`POST /<course>/rename`** — delete cascades
  the course's hierarchies (+ their nodes/coverage/attrs/targets) and its
  `course_objectives` (leaving orphaned `objectives` rows is fine — they're
  course-agnostic and interned by text; optionally prune unreferenced ones).
  Rename changes the id (the `/<course>` slug) across `courses`, `hierarchies`,
  `course_objectives`, and `coverage` (coverage is keyed by hierarchy, not
  course, so mostly the course-scoped tables) — or restrict to title-only rename
  if id-rename proves fiddly (decide at build time).

### C. Objectives: one upload, with a target

On the Objectives page, add a small `<select>` of the course's hierarchies
(default = primary reference) next to the upload button, and pass it through:
`import_objectives.load(DB_PATH, course, items, hierarchy=<selected>)`. Plain-text
(raw-only) files have no node ids, so the target is simply irrelevant for them —
no separate control needed. Update labels to read "upload raw objectives **or** a
categorization TSV (objective[, uuid] + node_id) → <target>".

### D. Slim the Data page

- **Move** hierarchy-loading off `/data` into the per-course Setup (§B).

- **Keep `/data`** for the global ops only: **Restore from version control** and
  **Export snapshot**, plus a short empty-state explainer. Consider renaming the
  sidebar-footer link **Settings**.

- **Empty-db landing**: `index()` should steer a fresh user to **create a
  course** (the sidebar `+`, e.g. auto-opened, or a minimal welcome panel) rather
  than the Data page. Keep restore reachable for the "I have an `export/`
  snapshot" case.

### E. Course outline & misc

- **Eager outline**: create the outline at course-creation (§A) via
  `ensure_outline` (idempotent) so a new course is immediately complete.

- **Help / docs**: refresh `help.html`, `README.md`, `CLAUDE.md` for the
  create → add-hierarchy → objectives → categorize → plan flow.

### F. Export / import a course (bundle)

A single file that fully recreates one course. Contents:

- the `courses` row (id, title);

- every hierarchy of the course: for each, its `hierarchies` registry row plus
  its `nodes` (references and the authored outline alike) and any `node_attr`
  (the outline's per-lesson learning objectives, etc.);

- the course's objectives (`objectives` text + `course_objectives` membership/
  order) and all `coverage` edges into the course's hierarchies;

- `hierarchy_targets` for the course's outline.

Format: a single JSON document (a course-scoped superset of the node-list and
planning tables). **Export** = serialize the above for one course; **import** =
recreate it transactionally, rejecting/﻿renaming on an id clash. Wire export on
the Setup page and import on the sidebar `+`. Build as an additive feature; the
`export/` TSV snapshot + `rebuild_db` path is untouched.

## Phasing

- **Phase 1 — core sidebar flow**: inline **create course** (§A, New course),
  the **Setup page** with add-hierarchy + **delete hierarchy** (§B, minus
  course-delete), the **objectives target dropdown** (§C), and the **slimmed Data
  page** + empty-state landing (§D). Eager outline (§E). This delivers the full
  happy path plus scope (a).

- **Phase 2 — course admin**: **delete/rename course** (§B, scope (b)) and
  **export/import course** (§F, incl. the sidebar `+` Import action).

- **Follow-ups**: the `hierarchy_targets` toggle (scope (c)); deciding whether the
  course bundle becomes the committed format (possibly retiring the `export/` TSV
  model); richer hierarchy edit/reorder.

## Proposed change set (each small, reviewable)

1. `POST /course/new` + sidebar `+` inline form; eager `ensure_outline`.

2. `GET /<course>/setup` + `templates/setup.html`; `POST /<course>/hierarchy/load`
   (shared helper with the old route; `<course>-<kind>` slug default); per-course
   ⚙ link in the sidebar; move the loaded-hierarchies table here.

3. `POST /<course>/hierarchy/<hierarchy>/delete` + confirm UI.

4. Objectives **target dropdown** in `objectives.html`; pass `hierarchy=` through
   `/<course>/objectives/upload`.

5. Slim `/data` to restore + export (+ rename to Settings); rework `index()`
   empty-state toward "create a course".

6. `POST /<course>/delete` (+ rename); cascade logic.

7. Course **bundle** export/import: serializer + `POST /<course>/export-bundle`
   (or a GET download), `POST /course/import`, sidebar `+` Import action.

8. Docs: `help.html`, `README.md`, `CLAUDE.md`.

Phase 1 = items 1–5; Phase 2 = items 6–7; item 8 spans both.

## Out of scope / follow-ups

- Editing `hierarchy_targets` (which references an outline is measured against).

- In-app authoring/editing of a reference hierarchy's nodes (references stay
  file-/bundle-sourced; authored structure is the outline, edited on the plan
  page).

- Bulk/multi-course operations; making the bundle the canonical committed format.
