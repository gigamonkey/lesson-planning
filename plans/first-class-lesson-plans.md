# First-class lesson plans

## Goal

Today a "lesson" in the outline is just a `## Title` heading: a positional slot
that carries a title, an optional learning objective, an optional duration, and a
list of placed objective bullets. We want each lesson to become a **lesson plan**
— a first-class, file-backed entity with rich free-text content organized into a
fixed set of parts:

- **Preview**
- **Learning objective**
- **Review**
- **Key ideas**
- **Expert thinking**
- **Guided practice**
- **Closure**
- **Independent practice**

Each part is essentially free-text markdown. A lesson plan is stored as its **own
markdown file** in the course directory. Like objectives, a lesson is identified
by a **uuid** so its title can change without changing its identity; unlike
objectives, **two lessons in one course may share a title** (the uuid, not the
text, is identity). Each lesson file is named `<slugified-title>-<shortid>.md`
where `shortid` is an abbreviated piece of the uuid.

Eventually we want UI for displaying and editing lesson plans; this plan lands the
storage, identity, and round-trip first, then sketches the UI as a later phase.

## The crux: lessons need persistent identity

The blocker is that **lessons have no durable identity today**. In
`plan_io.parse_plan` the lesson node_id is positional (`"1.1"`, `"1.2"`,
regenerated from heading order — see `plan_io.py:149-163`), and both `read_course`
and `load_plan_text` *nuke and rebuild* the entire outline from `plan.md` on every
load. That is fine only because a lesson carries no content worth preserving:
title, LO, duration, and placements all sit structurally in `plan.md` and are
re-derived each time. `import_structure` already mints uuids for outline nodes
(`plan_io.py:772,781`), but a single `render → parse` round-trip throws them away
and renumbers positionally.

The moment a lesson owns a file full of free-text content, positional ids become a
reorder trap (cf. the `editable-hierarchy-id-stability` memo): reorder two
lessons and their content follows the wrong slot. So the foundation of this
feature is:

> **Outline lessons get a stable, per-course uuid as their `node_id`**, carried
> through `plan.md` as an identity token (exactly the scheme objectives already
> use), and the outline reload becomes **content-preserving** (keyed by that uuid)
> instead of nuke-and-rebuild.

Units keep their positional ids for now — they own no file and no free-text
content, and their `parent_id` linkage is re-derived from document structure each
load. (Revisit if units later grow content of their own.)

## On-disk format

### Lesson files live in a `lessons/` subdirectory

```
my-courses/
  csa/
    ced.md                 # reference (unchanged)
    plan.md                # outline (gains lesson identity tokens; loses the LO line)
    objectives.tsv         # (unchanged)
    coverage.tsv           # (unchanged)
    lessons/
      decision-statements-7f3a9c21.md
      while-loops-2b1e0d44.md
```

A subdirectory keeps lesson files from being swept up as reference hierarchies:
`read_course` classifies every **top-level** `.md` as plan-or-reference via
`_md_files` (`plan_io.py:185-187`, a non-recursive `os.listdir`), so files under
`lessons/` are invisible to that classifier and we load them with a dedicated
pass. The filename is `<slug>-<shortid>.md`:

- `slug` = `slugify(title)` (lowercase; runs of non-alphanumerics → single `-`;
  trimmed; empty falls back to `lesson`).
- `shortid` = first 8 hex chars of the uuid (dashes dropped). Cosmetic + keeps
  same-titled lessons from colliding on disk; **not** parsed for identity.

### Lesson file contents

```markdown
---
uuid: 7f3a9c21-5d2e-4b8a-9c10-1a2b3c4d5e6f
title: Decision Statements
---

## Preview

Free-text markdown…

## Learning objective

Implement and trace `if` / `else if` / `else`.

## Review

…

## Key ideas

…

## Expert thinking

…

## Guided practice

…

## Closure

…

## Independent practice

…
```

- **`uuid:` is identity**, authoritative on load (pinned in front matter exactly
  as a reference's `slug:` is). The filename's `shortid` is a convenience; if it
  disagrees with the uuid we just rename on next write (optionally warn), never
  trust it.
- **`title:` mirrors the outline.** `plan.md`'s heading is the authoritative title
  (you rename a lesson in the outline, same as you reword an objective in a
  bullet); the file's `title:` is a human-readable mirror kept in sync on write,
  and the filename slug derives from it.
- The body is the eight parts as `## <Part>` H2 sections, in canonical order.
  **Only non-empty parts are written.** Unknown headings are preserved verbatim on
  round-trip if cheap to do, but the eight known parts are the contract.
- A lesson file holds **only the eight free-text parts** — not the placed
  objectives. Those stay in `plan.md` (see
  [Placed objectives stay in `plan.md`](#placed-objectives-stay-in-planmd)).

### `plan.md` gains a lesson identity token, loses the LO line

A lesson heading carries a trailing identity token, the **last** `(#<prefix>)`
group on the line — the same token grammar objectives use (`TOKEN_RE`,
`plan_io.py:37`), so "the trailing `(#…)` is identity" stays uniform across
bullets and lesson headings. The duration tag, when present, sits **before** the
token:

```markdown
# Unit: Selection

## Decision Statements (3 days) (#7f3a)

- Evaluate a boolean expression.  (#221a)
- Trace an if/else ladder.  (#bfff)

## While Loops (#2b1e)

- Write a sentinel-controlled loop.  (#5653)

# Unplaced objectives

- Brainstorm a class project.  (#9eec)
```

Aside from the new lesson token on each heading and the dropped LO line (below),
`plan.md` is structurally unchanged: it stays the one place that organizes the raw
objective pool — placed bullets under their lessons, unplaced bullets in the pool
tail. The lesson *file* holds the eight free-text parts only (see
[Placed objectives stay in `plan.md`](#placed-objectives-stay-in-planmd)).

Parsing order on a lesson heading: strip the trailing identity token first
(`TOKEN_RE`), then run `split_duration` on what remains. New tokens are minted by
`abbrev_tokens` over the set of lesson uuids (shortest unique prefix, floor 4) on
render — reusing `plan_io.abbrev_tokens` / `resolve_token` as-is.

**The `**Learning objective:**` line moves out of `plan.md` into the lesson file's
`## Learning objective` section** (it is one of the eight parts; a single source
of truth). To keep old courses loadable and to drive the migration, `parse_plan`
still *reads* a legacy `**Learning objective:**` line and uses it to seed the
lesson's LO content (then it stops being written to `plan.md`) — the same
"tolerate legacy, re-emit canonical" approach already used for the `## Pool`
heading.

### Placed objectives stay in `plan.md`

A lesson's **placed raw objectives** — the `(#token)` bullets under its heading,
which become `coverage` edges into the outline — **stay in `plan.md`**, exactly as
today. `plan.md` remains the single canvas where a course designer organizes the
raw-objective pool: placed bullets sit under their lessons, unplaced bullets in the
`# Unplaced objectives` tail, and the document order is the master pool order. The
**lesson plan is a distillation of those objectives, not their owner** — the lesson
file holds the eight free-text parts, and the lesson editor (Phase 2/3) *displays*
the associated raw objectives (read from the lesson's placements) for reference
while you write the plan.

This keeps everything about objectives unchanged from today: `parse_plan`'s bullet
handling, `_resolve_bullets` (`plan_io.py:228-272`), the master/per-node order
split, `objectives.tsv`, `coverage.tsv`, and the markdown outline editor's full
power to place and reorder objectives by editing `plan.md`. The *only* thing
leaving `plan.md` is the `**Learning objective:**` line (now the lesson file's
`## Learning objective` part).

The single subtlety it leaves (next section): a `plan.md`-only edit must not wipe
the lesson **content** (`node_attr` parts) that lives in the lesson files.
Placements are still rebuilt from `plan.md` as today — only the eight-part content
needs preserving.

## Database

**No schema change, no version bump.** Lesson content rides existing tables:

- A lesson's `node_id` becomes its **uuid** (it already may be — `import_structure`
  mints uuids; we just stop discarding them). `nodes`/`coverage`/`node_attr`/
  `node_duration` are unchanged in shape.
- The eight parts are stored in **`node_attr`**, one row per part:
  `name ∈ {preview, learning_objective, review, key_ideas, expert_thinking,
  guided_practice, closure, independent_practice}`, `value` = the part's
  free-text. `learning_objective` is already a `node_attr` name today
  (`plan_io.py:222-224`), so the calendar and outline views that read it keep
  working untouched — they just get their value from the lesson file now. The
  canonical part order lives in code (a constant list), so no order column is
  needed.

Reusing `node_attr` means **`course_bundle.py` already covers lesson content**
(it bundles nodes + attrs) — verify, but expect no change.

## Implementation

### `slugify` helper (`plan_io.py`)

Add a small `slugify(text)` (none exists today — confirmed by grep). Lowercase,
`[^a-z0-9]+` → `-`, strip leading/trailing `-`, fall back to `"lesson"` if empty.
Also a `lesson_shortid(uuid)` = first 8 hex chars.

### Reading lesson files (`plan_io.py`)

New `_read_lesson_files(course_dir)`: for each `lessons/*.md`, parse front matter
(`uuid`, `title`) and split the body into the known `## <Part>` sections. Return a
list of `{uuid, title, parts: {name: text}}`. Tolerate a missing `lessons/` dir
(returns `[]`). Lessons are course-owned like objectives; if a file's `uuid` is
already owned by another course, re-mint on load (mirror the objective re-mint in
`read_course`, `plan_io.py:376-378`) — or defer this guard to a follow-up if we
keep it simple at first.

### `parse_plan` returns lesson tokens

Extend the `lessons` tuples to carry the heading token:
`(token|None, parent_unit_id, title, duration)` — drop the synthesized positional
id from the tuple; the real `node_id` (uuid) is resolved later. Keep reading a
legacy `**Learning objective:**` line into a `los` map for migration, but it is no
longer the durable home of the LO.

### Resolving lessons → uuids (`read_course` and `load_plan_text`)

Add a `_resolve_lessons(...)` step parallel to `_resolve_bullets`:

- Resolve each heading token against the known lesson-uuid set (the lesson files
  in `read_course`; the course's existing lesson nodes in `load_plan_text`).
- A token that resolves → that uuid (and the heading text is adopted as the title,
  token-wins, exactly like objective text).
- No token / ambiguous → mint a fresh uuid (a brand-new lesson typed into the
  outline). On the next `write_course` it gets a file.

`_rebuild_outline_nodes` then inserts lesson nodes with `node_id = uuid`,
`parent_id =` the unit's positional id, `ordinal` from document order. Durations
stay as today. The LO (and the other parts) are written to `node_attr` from the
lesson-file content in `read_course`; in `load_plan_text` they are **preserved**
(see next).

### Make the outline reload content-preserving

This is the subtle, essential part. Today both loaders do
`DELETE FROM node_attr WHERE course=? AND hierarchy=?` then rebuild from `plan.md`
(`plan_io.py:316,481`). Once lessons own durable content in `node_attr`, a blanket
delete on a `plan.md`-only edit (the markdown outline editor's save path:
`load_plan_text` then `write_course`) would erase every lesson's content.

Because placements stay in `plan.md`, the *only* thing that must survive a
`plan.md`-only edit is the eight-part lesson **content**. With stable lesson uuids
we reconcile `node_attr` instead of blanket-deleting it. In `load_plan_text` (the
markdown-editor save path, which sees only `plan.md`):

- Compute the new lesson-uuid set from the resolved headings.
- Rebuild structure, placements (from the bullets), and the pool/unplaced/master
  order from `plan.md` — **exactly as today** (placements are still authored in
  `plan.md`, so nothing changes here).
- The one change: instead of `DELETE FROM node_attr WHERE course=? AND hierarchy=?`,
  delete `node_attr` only for lesson uuids **not** in the new set (genuinely removed
  lessons). Surviving lessons keep their content `node_attr` untouched — `plan.md`
  is not the source of truth for lesson content, so re-deriving the outline from it
  must not clobber the parts the lesson files own.

In `read_course` (full disk load) the lesson content *is* reloaded from the lesson
files into `node_attr` (the faithful disk→db load), while placements load from
`plan.md`'s bullets via `_resolve_bullets`, all unchanged.

### Rendering (`render_course`) and reconciling files (`write_course`)

`render_course`:

- Emit each lesson heading as `## <title>(<dur>) (#<token>)`, lesson tokens from
  `abbrev_tokens` over the lesson uuids. Keep emitting the per-lesson objective
  bullets and the `# Unplaced objectives` section exactly as today; only stop
  emitting the `**Learning objective:**` line (it now lives in the lesson file).
- Build a lesson file per lesson: front matter (`uuid`, `title`) + each non-empty
  part as `## <Part>` in canonical order. No objectives section. Return them in the
  files dict under `lessons/<slug>-<shortid>.md` keys (extend the current
  `{plan.md, objectives.tsv, coverage.tsv}` return).

`write_course` reconciles the `lessons/` directory (it currently only writes a
flat file set — `plan_io.py:697-704`):

- Build the desired `{uuid → path}` from the rendered files.
- Scan existing `lessons/*.md`, reading each file's front-matter `uuid`.
- **Rename** when the same uuid maps to a different filename (a retitle changed the
  slug); **write** current files; **delete** lesson files whose uuid is no longer
  in the outline. Deletion is destructive — log what is removed; an "archive
  instead of delete" option can come later if desired.

A lesson in the **`# Unassigned lessons`** section counts as "in the outline" and
is kept: it is a real lesson node, just with `parent_id = NULL` (`parse_plan`
appends it to the same `lessons` list with a `u.N` slot — `plan_io.py:157-163` —
and `render_course` round-trips it under that H1). It gets a uuid and a lesson
file like any other lesson, and the reconcile set includes parent-less lessons. A
lesson's file is deleted **only** when its heading disappears from `plan.md`
entirely — moving a lesson into (or leaving it in) the unassigned section never
deletes it.

Renames/deletes are picked up by the collab/autosave git commit automatically
(`commit_repo` stages the whole tree).

### Migration of existing courses

On the first `read_course` after this lands, a lesson with a legacy
`**Learning objective:**` line but no lesson file is loaded normally (LO into
`node_attr`); the next `write_course` then materializes a lesson file (with the LO
as its `## Learning objective` section) and rewrites `plan.md` with identity
tokens and no LO line. So the migration is "load old → save once." No separate
migration script needed; document it and trigger a save (e.g. the existing
restore-from-courses-directory action).

## Phasing

1. **Identity + storage round-trip (no UI).** `slugify`/`shortid`, lesson-file
   read/write, lesson tokens in `plan.md`, uuid lesson node_ids, content-preserving
   reload, `render_course`/`write_course` reconcile, LO migration. Update
   `FORMAT.md` (new "Lesson plans" section; amend the outline section — lesson
   headings now carry a token and the LO line is gone; objective bullets stay) and
   `CLAUDE.md`. Extend
   `test_plan_io.py` with round-trip checks (below). **This is the bulk of the
   work and is independently valuable** — it makes lesson content durable.

2. **Read-only lesson view.** A `/<course>/lesson/<uuid>` page rendering the eight
   parts (markdown → HTML), linked from each lesson in the outline and from the
   calendar day cell. No editing yet.

3. **Lesson editing UI.** Per-part editors (the natural shape is a CodeMirror/
   textarea per part, or one "edit as markdown" surface over the whole lesson
   file, mirroring the existing outline markdown editor). Saving posts the lesson
   file content → `node_attr` → `write_course`, the same load-then-write pattern
   the outline editor already uses (`/<course>/outline/source`).

## Testing

Add to `test_plan_io.py`:

- **Lesson identity round-trips:** load a course with lesson files → render →
  reload; lesson uuids, titles, and all eight parts are byte-stable; `plan.md`
  tokens are stable.
- **Rename preserves identity + content:** change a lesson title, save; the file
  is renamed (uuid unchanged), content intact, `coverage` placements intact.
- **Reorder preserves content:** swap two lessons in `plan.md`, save; each lesson's
  content stays with its uuid (the reorder-trap regression test).
- **Outline-only edit preserves content:** run `load_plan_text` on a `plan.md`
  edit that touches no lesson content (e.g. reword a bullet, reorder a placement);
  each surviving lesson's `node_attr` parts survive (the content-preserving-reload
  guarantee), while placements update from `plan.md` as today.
- **Placements still round-trip via `plan.md`:** the existing objective
  placement/pool/order round-trip checks keep passing unchanged (regression guard
  that keeping bullets in `plan.md` didn't disturb them).
- **New lesson mints a file:** type a tokenless `## New lesson` into `plan.md`,
  save; a fresh uuid + lesson file appear.
- **Delete removes the file:** drop a lesson heading, save; its file is deleted.
- **Legacy migration:** a `plan.md` with a `**Learning objective:**` line and no
  lesson file loads, then on save produces a lesson file with the LO section and a
  tokened, LO-free heading.

## Open questions / decisions (recommendations in **bold**)

1. **Learning objective home.** Move it into the lesson file as the
   `## Learning objective` part (**recommended** — single source of truth; the
   calendar/outline keep reading the same `node_attr`), vs. keep it duplicated in
   `plan.md`. Moving it is what makes the content-preserving reload necessary.

2. **Lesson content storage.** Reuse `node_attr` (**recommended** — no schema bump,
   `course_bundle` already covers it) vs. a dedicated `lesson_content` table.

2a. **Where placed objectives live.** *Decided:* keep them as bullets in `plan.md`
   (status quo) so it stays the one canvas for organizing the raw-objective pool
   (placed + unplaced together); the lesson plan is a distillation the editor
   *displays* the associated objectives beside, not their owner. (The alternative —
   moving them into each lesson file — would make lesson files self-contained but
   split the pool across files, force the master order to be re-derived across
   files, and demote the markdown outline editor to structure-only. Rejected.)

3. **Lesson file location.** `lessons/` subdirectory (**recommended** — keeps the
   reference classifier non-recursive and unchanged) vs. top-level with a naming
   convention the classifier must learn to skip.

4. **Token placement on the heading.** Identity token last, duration before it
   (**recommended** — keeps `TOKEN_RE`'s "trailing `(#…)` is identity" invariant
   uniform with bullets) vs. duration last.

5. **Deletion policy.** Delete the file when a lesson leaves the outline
   (**recommended for v1**, with a log line) vs. archive to a `lessons/archive/`
   graveyard.

6. **Units.** Keep positional (**recommended** — no per-unit content) vs. also give
   them uuids for symmetry.
