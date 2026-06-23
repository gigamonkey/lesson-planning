# Plan: Edit the course outline as Markdown

## Goal

Add an in-app **Markdown editor** for the course outline. Today the outline
(`plan.md`) is only editable through the structured htmx widgets on the workspace
(unit/lesson/objective routes); the raw markdown is never surfaced. This feature
lets the user open the outline's round-trippable markdown in a real code editor,
edit it freely — reorder units, reword lessons, move/retitle objective bullets,
edit the learning-objective lines and front-matter wiring — and **save**.

A save does two things, leaving the course **clean and up to date**:

1. writes the edited markdown to `plan.md` on disk (canonicalized), and
2. refreshes the database from it,

so that after a save the DB renders byte-identically to what is on disk
(`plan_io.is_dirty` returns false) and the workspace reflects the edits.

The editor is **fairly full-featured**: syntax highlighting, line numbers,
undo/redo, search, and a complete movement + copy/cut/paste keymap, via
**CodeMirror 6** (see *Editor technology* below).

## What the user is editing

The editor operates on the **round-trippable `plan.md`** — the storage form
produced by `plan_io.render_course()` — *not* the one-way rendered report from
`render_outline.py` (the existing `/<course>/outline.md` route). The format is
exactly what `FORMAT.md` / `plans/done/markdown-as-storage.md` specify:

```markdown
---
course: widgets
title: Widgets 101
primary_reference: widgets-ced
primary_outline: widgets-plan
targets: widgets-ced
---

# Unit 1: Widget Basics

## 1.1 What Is a Widget

**Learning objective:** Describe a widget and name its parts.

- Name the two main parts of a widget.  (#a3f2)
- Explain what the frobnicator does.  (#1b9e)

## Pool — not yet placed

- Brainstorm a class project that uses widgets.  (#e2a1)
```

Everything in this file is already a clean serialization/parse pair:

- **`plan_io.render_course(conn, course)`** → the `plan.md` text (plus the two
  TSVs) in memory. This is what the editor *loads*.
- **`plan_io.parse_plan(text)`** → `(meta, units, lessons, los, bullets)`, and
  **`plan_io.read_course()`** turns that into DB rows. This is what a *save* runs.

The crucial mechanic that makes free-text editing safe is the **objective identity
token** (`(#a3f2)`): identity rides on the trailing token, not the bullet text, so
the user can reword a bullet in place and keep its coverage. A bullet with no
token (or a hand-mangled one) is interned as a *new* objective on load. Unit and
lesson ids are positional (`1`, `1.1`, …) and regenerated on every parse, so
reordering/retitling "just works" and the structural placements travel with the
headings. This is the design already proven by the load→export→load fixpoint in
`plans/done/markdown-as-storage.md`; the editor is simply a UI in front of it.

## The save flow

The save reuses the existing disk↔DB pair. Ordering matters because `read_course`
reads the *whole* course directory (references + `objectives.tsv` + `coverage.tsv`),
and a freshly-typed bullet has no entry in `objectives.tsv` yet. Two viable
shapes:

**Recommended — in-memory load, then canonical write:**

1. Add `plan_io.load_plan_text(db_path, course, text)` — a thin extraction of the
   plan-loading core already inside `read_course` (lines ~181–290): parse the
   text, scoped-reset the outline hierarchy + this course's pool/placements,
   rebuild units/lessons/`node_attr`, and resolve each bullet's token **against
   the course's existing `objectives` rows in the DB** (not against an on-disk
   TSV), interning new/tokenless bullets as fresh uuids. Reference hierarchies and
   reference `coverage` rows are left untouched.
2. `plan_io.write_course(db_path, course, course_dir)` — the existing export:
   re-serialize the DB to canonical `plan.md` + `objectives.tsv` + `coverage.tsv`
   on disk (assigning tokens to the new objectives, writing reference `.md` files
   if absent so the corpus stays self-contained).

   After (2), the on-disk `plan.md` is the *canonical* render — which may differ
   cosmetically from the user's exact keystrokes (token assignment, blank-line
   normalization, pool ordering). The editor reloads this canonical text on save
   success so the buffer matches disk.

This avoids the "directory must already be complete before `read_course` can run"
ordering hazard, because the disk side is produced by `write_course` from the DB,
not consumed by `read_course` from a half-written directory.

**Refactor note:** factor `read_course` so its plan-loading body becomes
`load_plan_text` (operating on a passed connection + text), and `read_course`
calls it after seeding `objectives` from the TSV. The two callers differ only in
*where the objective registry comes from* (TSV on disk vs. the live DB), so the
parse + rebuild logic is shared verbatim.

### Why not write the raw text to disk first

The alternative — write the user's exact bytes to `plan.md`, then
`read_course` + `write_course` — also works and the user explicitly wants the
text on disk. But `read_course` needs the rest of the directory present and
correct, and a brand-new course may have no corpus dir yet. Producing the disk
files *from* the post-load DB (the recommended flow) sidesteps that and still
ends with the edited content on disk, just canonicalized. If preserving exact
keystrokes on disk ever matters more than canonical form, swap to: write raw →
`read_course` → `write_course`, accepting the bootstrapping caveat. Call this out
but default to the recommended flow.

### Result

After save: DB updated, `plan.md` + TSVs written, `is_dirty` false, the save-state
icon in the sidebar clean, and the workspace (units/lessons/pool) reflects the
edits on next view.

## Editor technology

**CodeMirror 6**, bundled locally. This is the user's call (CM5 rejected; CM6 or
Monaco, build step acceptable). CM6 is the lighter, better fit for a single
markdown buffer; Monaco is heavier and more IDE-oriented. Recommend CM6, with
Monaco as a drop-in alternative if its features are later wanted.

This adds the project's **first JS build step**, a deliberate departure from the
current CDN-only frontend (htmx, bootstrap-icons). Keep it minimal:

- `package.json` + a one-line **esbuild** bundle (no config file): bundle a small
  `frontend/editor.js` entry (imports `codemirror`, `@codemirror/lang-markdown`,
  `@codemirror/commands`, `@codemirror/search`, the default keymap, and the
  modal/emacs keymaps — `@replit/codemirror-vim` and `@replit/codemirror-emacs`,
  CM6's de-facto Vim/Emacs packages) into a single committed
  `static/editor.bundle.js` (+ any CSS).
- **Commit the bundle.** Runtime stays node-free (matches the "files are the
  committed truth, the rest is cache" ethos and means `uv run app.py` needs no
  npm). `npm run build` is a dev-time step, documented in `CLAUDE.md` / `README.md`
  and `.gitignore`'d for `node_modules/`.

CM6's default keymap (`defaultKeymap` + `historyKeymap` + `searchKeymap`) already
covers everything the user asked for: word/line/document movement, copy/cut/paste,
undo/redo, find/replace, multiple selections. **Keybinding scheme:** ship the
default keymap plus **Vim** (`@replit/codemirror-vim`) and **Emacs**
(`@replit/codemirror-emacs`) as alternatives, chosen from a small keymap selector
(Default / Vim / Emacs) persisted in `localStorage`. The Vim/Emacs keymaps are
installed as a CM6 extension swapped via a compartment so switching needs no
editor rebuild. Any subset can be dropped if scope needs trimming; the default
keymap alone satisfies "at least basic keybindings."

## UI / routes (`app.py` + templates)

A dedicated full-height editor page, reachable from the outline workspace and the
sidebar (next to the existing per-course controls):

- **`GET /<course>/outline/edit`** → renders `templates/outline_edit.html`: the
  CM6 editor seeded with `render_course(conn, course)[…][PLAN_FILE]`, a Save
  button, a Revert/Reload button, the keymap selector, and an inline
  error/flash area. A "dirty since last save" indicator driven by CM6's doc-change
  events.
- **`POST /<course>/outline/source`** → the save endpoint. Body = the edited
  markdown. Runs `load_plan_text` then `write_course` (see save flow). On success
  returns the canonical `plan.md` text (so the editor can replace its buffer) +
  fresh dirty state; on parse/load failure returns the error **without writing**
  anything, so a bad edit never clobbers the course.
- Optionally **`GET /<course>/outline/source`** (raw text/markdown) for symmetry
  and external tooling — the editable counterpart to the report-only
  `/<course>/outline.md`.

Guardrails in the route:

- **Front matter is editable but load-validated.** `course:` must be present and
  must match the URL's `<course>` (changing course identity via this editor is out
  of scope — reject with a clear message). `title`, `primary_reference`,
  `primary_outline`, and `targets` flow through to the `courses` row /
  `hierarchy_targets` exactly as `read_course` already applies them.
- **Unknown reference slugs** in `targets` / `primary_reference` are reported, not
  silently dropped (mirror the existing upload reporting).
- Wrap the load in a try/except so a malformed buffer yields a flash + inline
  error and leaves the DB and disk untouched.

Navigation: add an "Edit as Markdown" link on the outline workspace header and a
small pencil/`</>` affordance on the course's sidebar block.

## What does NOT change

- **No schema change.** Same eight tables; this is a UI + a small `plan_io`
  refactor over the existing serialize/parse pair.
- **`render_outline.py`** and `/<course>/outline.md` (the one-way report) are
  untouched — the editor deliberately edits the *storage* `plan.md`, not the
  report.
- The structured htmx outline widgets keep working; the markdown editor is an
  alternative view over the same DB, not a replacement. (Note the two are not
  live-synced: editing markdown and saving rebuilds the outline; the workspace
  reflects it on its next load.)
- Reference hierarchy markdown stays load-only; the editor never edits references.

## Phasing

1. **`plan_io.load_plan_text` + refactor.** Extract the plan-loading core from
   `read_course`; add `load_plan_text(db_path, course, text)` resolving tokens
   against the live DB. Unit-test the round-trip: `render_course` → edit a buffer
   → `load_plan_text` → `write_course` is a fixpoint, and a reworded-but-tokened
   bullet keeps its coverage while a tokenless bullet mints a new objective.
2. **Save route.** `GET /<course>/outline/edit` (serving current text via a plain
   `<textarea>` first) + `POST /<course>/outline/source` wiring the save flow,
   with the front-matter/reference guardrails and no-clobber-on-error behavior.
   Verifiable end-to-end before any CM6 work.
3. **CodeMirror 6.** Add `package.json` + esbuild, the `frontend/editor.js` entry,
   build `static/editor.bundle.js`, and swap the textarea for CM6 (markdown mode,
   line numbers, history, search, default keymap). Document `npm run build`.
4. **Keymap selector + polish.** Vim and Emacs keymap options (compartment-swapped,
   localStorage-persisted), dirty indicator, Revert button, sidebar/workspace
   entry points, error styling.

Phases 1–2 ship a working (if plain) markdown editor with no frontend build;
phases 3–4 layer on the full-featured editor.

## Open / deferred

- **Concurrent edits.** Markdown editing and the htmx widgets both write the same
  outline; there's no live sync. A save from one stomps unsaved state in the
  other. Acceptable for a single-user tool; note it, don't solve it.
- **Exact-keystroke persistence.** The recommended flow writes the *canonical*
  render to disk, not the user's literal bytes. If literal preservation is wanted,
  switch to write-raw-then-reload (see save flow) and accept the bootstrapping
  caveat.
- **Editing reference hierarchies.** Same editor could later edit a reference
  `.md`, but that needs the generic flavor-aware `nodes → markdown` writer the
  storage plan deliberately avoided. Out of scope.
- **Live preview / split pane.** A rendered-markdown preview alongside the editor
  is a natural later add; not in this plan.
