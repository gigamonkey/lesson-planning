# Elevate the lesson plan as the "Course outline"

## Motivation

We made the lesson plan "just another hierarchy" (an editable `kind='lesson-plan'`
hierarchy). That generality is good and stays. But conceptually this tool is
**about authoring a course outline that references the official hierarchies** (the
CED, the book, the IB syllabus). The UI should reflect that: the course outline is
the primary thing you work on; references and any other editable hierarchies
(e.g. a future editable book) are supporting material.

Two concrete asks:

1. Rename the lesson-plan hierarchy to **"Course outline"** in the UI.
2. In the sidebar, make it a **distinguished first item under each course, above
   "Objectives"**.

## Current state (for grounding)

- The outline is one hierarchy per course: `kind='lesson-plan'`, `editable=1`,
  slug `<course>-plan` (e.g. `csa-plan`), title `"<COURSE> Lesson Plan"`.
- Resolved by `outline_hierarchy(conn, course)` — `editable=1 ORDER BY
  (kind='lesson-plan') DESC`. Created on demand by `ensure_outline()`.
- The `plan(course)` route ensures the outline exists and redirects to its
  workspace (`hierarchy_view`). The workspace's editable branch is the
  units/lessons board.
- Sidebar (`base.html`, fed by the `inject_nav` context processor): under each
  course it lists **Objectives** first, then `c.hierarchies` ordered
  `editable, (kind='ced') DESC, hierarchy` — so the outline currently sorts
  **last**.
- "lesson-plan" / "Lesson Plan" / "plan" surface in: `outline_hierarchy` /
  `ensure_outline` (kind + title), the Stage-1/Stage-2 migrations, the
  `.pill-lesson-plan` CSS (two copies) + the objectives-table header abbreviation
  `{'lesson-plan': 'plan'}`, the workspace intro ("Edit the plan…"), and help.html.

## The change

### 1. Naming → "Course outline"

Decision: **rename the internal kind `lesson-plan` → `course-outline`** (not just
the display label), so the data and code stop saying "lesson-plan" while the UI
says "Course outline". This is a small, safe migration like the earlier kind
splits. The **slug stays `<course>-plan`** (opaque handle; renaming it would touch
`nodes`/`coverage`/`node_attr`/`hierarchy_targets` for no real gain).

- Migration (in `ensure_schema`, idempotent): `UPDATE hierarchies SET
  kind='course-outline', title='Course outline' WHERE kind='lesson-plan'`.
- `ensure_outline()`: insert `kind='course-outline'`, `title='Course outline'`.
- `outline_hierarchy()`: prefer `(kind='course-outline')`.
- CSS: rename `.pill-lesson-plan` → `.pill-course-outline` (both copies; keep the
  green palette).
- Objectives-table header abbreviation: `{'course-outline': 'outline',
  'ib-syllabus': 'ib'}` (was `'lesson-plan': 'plan'`).
- Title display: store `'Course outline'` (course-agnostic; the course is already
  the sidebar/section context). The workspace page `<h2>`/title shows it.
- Re-export the planning snapshot afterward (the committed `hierarchies.tsv`
  carries the outline row's kind/title).

Alternative considered: UI-only relabel, keeping `kind='lesson-plan'`. Lower
churn, but leaves "lesson-plan"/"plan" scattered through code, data, and the
export — rejected for the small extra migration cost.

Out of scope: the app's own name ("Lesson Planning" in the sidebar `<h1>` and
page title). Leave it for now; revisit separately if we want to rebrand the tool.

### 2. Sidebar elevation

Target layout under each course:

```
CSA
AP Computer Science A
  ▸ Course outline        <- distinguished, first (links to the plan route)
    Objectives
    AP CSA CED   [ced]
    CSA BOOK     [book]
    (editable book draft, if any) [book]
```

- **Course outline is rendered from the course, not from the hierarchy list**, so
  it shows for **every** course even before an outline exists. Its link is
  `url_for('plan', course=c.course)` — that route `ensure_outline()`s and
  redirects into the workspace, so clicking it on a fresh course starts authoring.
- The context processor (`inject_nav`) splits each course's hierarchies into the
  **course outline** (the `editable=1, kind='course-outline'` one — its slug, for
  active-state highlighting) and **everything else** (`others`). The sidebar then
  renders: Course outline → Objectives → `others` (references + any other
  editable hierarchies, in the existing order).
- Active highlighting: the Course outline item is active when
  `active_hierarchy == <course's outline slug>`.
- **Distinguished styling**: bold, slightly set apart from Objectives and the
  reference list (e.g. a small leading icon and/or a thin divider below it),
  while keeping the established color convention (blue link, black when active).
  Exact treatment to be tuned during implementation.

Other editable hierarchies (a future editable book, `editable=1` but not
`kind='course-outline'`) are NOT elevated — they live in `others` like any
hierarchy. Only the course outline is promoted.

### 3. Supporting copy

- Workspace editable intro: "Edit the plan: create units, add lessons…" →
  "Build the course outline: create units, add lessons…".
- help.html: rename the "Plan" entry to "Course outline" and adjust the intro
  prose ("…organize them into a traceable lesson plan" → "course outline").

### 4. Optional: landing on the outline

Because the tool is fundamentally about the course outline, consider redirecting
`/` (and/or `/<course>`) to the **course outline** instead of the default
reference workspace. This is a behavior change, so treat it as a separate
decision — recommended, but easy to defer.

## Files touched

- `lesson-planning/app.py` — `ensure_schema` migration; `ensure_outline` /
  `outline_hierarchy` (kind + title); `inject_nav` (split outline vs others);
  objectives-table abbreviation; (optional) `index`/`tree` redirect target.
- `lesson-planning/templates/base.html` — sidebar course block (Course outline
  first item); `.pill-lesson-plan` → `.pill-course-outline` (×2); distinguished
  styling.
- `lesson-planning/templates/workspace.html` — editable intro copy.
- `lesson-planning/templates/help.html` — "Course outline" entry + prose.
- `lesson-planning/export/hierarchies.tsv` — regenerated after the kind/title
  migration runs on the live db.

## Edge cases / notes

- **No outline yet**: handled by linking the sidebar item to the `plan` route
  (create-on-demand). No need to pre-create empty outlines.
- **Idempotent migration**: the kind/title `UPDATE` is a no-op once renamed.
- **Resolution stays robust**: `outline_hierarchy` still finds the outline by
  `editable=1` preferring the renamed kind, so a course with only an editable book
  (and no course outline) won't mis-resolve.
- **Multiple course outlines per course**: not supported (one is assumed). The
  schema allows it; the app picks one. Out of scope.

## Out of scope / future

- Renaming the app itself ("Lesson Planning").
- A dedicated editor for non-outline editable hierarchies (the editable book —
  Stage 2 of unified-hierarchies).
- Renaming the `<course>-plan` slug.
