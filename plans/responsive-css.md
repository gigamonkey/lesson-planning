# Responsive layout: make the site usable on a phone

The app is currently built for a wide desktop window: a fixed **345px sidebar**
pinned full-height on the left (`base.html` `.sidebar`), and a `main` area that
fills the rest with multi-column grids (`.board`, `.workspace-board`) and
fixed-width tables (the calendar's five 6.25rem day columns, the objectives
matrix). On a phone none of this fits — the sidebar alone is most of the
viewport, and the two-column boards overflow horizontally.

**Goal:** make it *reasonable to browse* the site on a phone. Reading the
outline, the references, the objectives table, and the calendar should all work
well at ~375px wide. **Editing** (drag-and-drop, inline rename, the Markdown
editor) is secondary — it should remain *possible*, not necessarily pleasant.

This is almost entirely a **CSS** change. The markup is server-rendered and
already mostly fine; we add one mobile top bar, a drawer toggle, and a single
`@media` block. No build step is involved (the CSS lives inline in `base.html`'s
`<style>`; the CodeMirror bundle is untouched).

## The navigation model on narrow screens

The user's instinct is right: on a phone the sidebar and the content can't share
the screen. The model is **two states**:

1. **Browsing the nav** — the sidebar (courses → references / outline /
   objectives / calendar) fills the screen.

2. **Viewing a page** — the selected page fills the screen; the sidebar is
   collapsed behind a **hamburger** in a slim top bar.

The wrinkle: there is no "nav-only" route today. `/` redirects straight to the
first course's outline (`app.py:628` `index`), and every page renders the
sidebar *and* `main` together via `base.html`. So we don't get state (1) for
free.

### Recommended approach: off-canvas drawer (pure CSS + tiny JS)

Keep the server rendering both regions on every page. On narrow screens:

- The sidebar becomes an **off-canvas drawer**: `position: fixed`, full height,
  `transform: translateX(-100%)` (off-screen by default), sliding in over a
  dimmed backdrop when opened. It already has `overflow-y: auto`, so its long
  content scrolls.

- Add a **mobile top bar** (hidden on desktop) with a hamburger button on the
  left and the current `page_title` as a label. Tapping the hamburger toggles a
  `nav-open` class on `<body>` (or toggles a hidden checkbox — see below);
  tapping the backdrop or a sidebar link closes it.

- `main` goes full width (drop the flex sidebar from the flow).

This gives state (2) directly. For state (1) — landing on the nav — the simplest
move is: **the drawer starts open when there is no meaningful page selected.**
Concretely, when the user first lands we can open the drawer so they pick a
course/page, then it closes on navigation. Two ways to decide "open on load":

- **Server hint (preferred):** `index` already knows whether a course is
  selected. Have templates set a body class like `nav-open` when the current
  route is the bare landing/empty-db case, or pass an `open_nav` flag. Minimal:
  on the empty-db `data` redirect and on first `/` hit, render with the drawer
  open.

- **Pure client:** default the drawer closed; the user taps the hamburger. Less
  slick but zero server changes. Acceptable for v1.

**Toggle mechanism (decided): a tiny JS toggle** that adds/removes
`body.nav-open`. It matches the existing inline scripts in `base.html`, which we
extend rather than add a new file. The checkbox-hack
(`<input type=checkbox hidden> + label`) was considered but rejected — it avoids
JS but is fiddlier to close-on-link-click, and we need a little JS anyway to
close the drawer when a sidebar link is tapped.

### Alternative considered: a real nav-only landing page

Add a `/` landing template that renders only the course list (no `main`), so
state (1) is a genuine page. Cleaner conceptually, but it's a new route + new
template + new desktop behavior to reconcile (desktop doesn't want a nav-only
page). **Not recommended** — the drawer covers the same need with far less
surface area and no desktop regression.

## Breakpoint

One breakpoint is enough. Use `@media (max-width: 48rem)` (~768px) as the
"narrow" cutoff — phones in portrait and small landscape fall under it; tablets
and desktop keep the current two-pane layout. All mobile rules live in this one
block at the end of `base.html`'s `<style>`, plus a couple of always-present
rules for the (desktop-hidden) top bar.

The single biggest desktop-only assumption to override is
`.layout { display: flex }` with the fixed-width sticky `.sidebar`. Everything
else cascades from making the sidebar a drawer and `main` full-width.

## Work items

### 1. Mobile top bar + drawer chrome (`base.html`)

- Add a `<header class="topbar">` just inside `<body>` (before `.layout`) with a
  hamburger button and a title slot. Hidden by default (`display: none`); shown
  only inside the `@media` block.

- Add a `<div class="nav-backdrop">` element (or `::after` on body) for the
  dimmed overlay; visible only when `body.nav-open` and narrow.

- Extend the existing inline `<script>` to (a) toggle `body.nav-open` on
  hamburger click, (b) close it on backdrop click, and (c) close it when any
  link inside `.sidebar` is clicked (so navigating to a page returns to the
  content view). Use event delegation to keep it small.

### 2. The `@media (max-width: 48rem)` block (`base.html`)

- `.topbar { display: flex }` — show the bar; give `body` top padding equal to
  its height (or make the bar sticky at top).

- `.layout { display: block }` — stop the flex side-by-side.

- `.sidebar` → off-canvas drawer: `position: fixed; top: 0; left: 0; height:
  100vh; width: min(85vw, 345px); z-index: 50; transform: translateX(-100%);
  transition: transform .2s;`. `body.nav-open .sidebar { transform: none }`.

- `.nav-backdrop` shown under the drawer when open; closes on tap.

- `main { padding: 1rem }` (tighter than the desktop `1.2rem 1.5rem`).

- Collapse the **boards to one column**: `.board, .workspace-board {
  grid-template-columns: 1fr; }` and drop the `.pool { position: sticky }` so
  the raw-objectives pool flows under the outline instead of beside it.

- The `.pagetitle` already renders in `main`; on mobile we may hide it in favor
  of the top-bar title, or keep it — decide during implementation (keeping it is
  simpler and fine).

### 3. The calendar (`templates/calendar.html`)

The calendar is the hardest case: it's deliberately a **fixed-width** grid
(`.cal-unit` is `calc(8rem + .5rem + (6.25rem * 5) + (.3rem * 4))` ≈ 600px; the
day grid is five rigid 6.25rem columns). It can't reflow to a phone without
losing its "week = row, weekday = column" meaning.

Recommended: **let it scroll horizontally** on narrow screens rather than trying
to reflow it. Wrap the calendar content in a container with `overflow-x: auto`
(its template owns its `<style>`, so the rule goes there inside an `@media`), and
shrink the left `.cal-when` label column (`flex: 0 0 8rem` → something like
`5rem`) to claw back width. The grid keeps its shape; the user swipes
horizontally. This preserves legibility at the cost of a scroll — acceptable for
a browse-focused goal.

A reflow-to-list alternative (each day a stacked block) is a much bigger change
and loses the calendar's whole point; defer unless the scroll proves unusable.

### 4. The objectives matrix (`templates/objectives.html`)

`table.objtable` is `width: fit-content` with one column per hierarchy and a
text column capped at `--content-max` (40rem). On a phone the text column alone
can exceed the viewport.

- The `.objbar` (new-objective form + search + download + import) is a flex row
  that will wrap; let it (`flex-wrap: wrap`) and make the inputs full-width-ish
  so the controls stack cleanly.

- The table: wrap in `overflow-x: auto` and lower `--content-max` on mobile (a
  `:root` override inside the `@media` block, e.g. `--content-max: 22rem`) so the
  text column wraps sooner and the hierarchy/pill columns stay reachable by
  horizontal scroll.

### 5. Touch & hover-only affordances (cross-cutting)

Many controls are **revealed on hover** — the reference drag handle
(`.draghandle`), per-row trash (`.hdel`), save/cancel on inline edits
(`.objedit button`, `.lessonhead` controls). Touch devices have no hover, so
these are invisible/unreachable.

- For the mobile block, make the most important ones **always visible**
  (`visibility: visible`) rather than hover-gated: at minimum the per-hierarchy
  and per-objective add/trash controls and the inline-edit save buttons. Use
  `@media (hover: none)` to scope "always show" rules to genuine touch devices
  without affecting a narrow desktop window.

- **Drag-and-drop**: SortableJS is configured with `forceFallback: true`, which
  gives it touch support already, but dragging on a phone while the page scrolls
  is awkward. This is editing — secondary. Don't invest here for v1; just verify
  it doesn't crash. Note in the PR that reordering on touch is "possible, not
  great."

- Tap targets: bump the tiny icon buttons (`.miniadd`, `.gear`, `.iconbtn`) to a
  ≥40px hit area inside the touch media query (padding, not font-size, to avoid
  reflowing the desktop look).

### 6. The Markdown editor (`templates/outline_edit.html`) and login

- `outline_edit.html` (CodeMirror) — give it a sensible mobile height and
  full-width container; CodeMirror itself handles touch. Low effort, verify only.

- `login.html` already has its own viewport meta and is a simple centered form;
  spot-check it but it likely needs nothing.

## Out of scope / explicitly deferred

- Reflowing the calendar into a stacked day list (scroll instead).
- A polished touch drag-and-drop experience.
- A dedicated nav-only landing route (the drawer replaces it).
- Any change to the data model, routes (beyond an optional `open_nav` flag), or
  the JS that drives drag/htmx.

## Validation

Run `uv run app.py` and use the browser at a 375px-wide viewport (or device
emulation). Check each route:

- Outline (`/<course>`), references (`/<course>/h/<hierarchy>`): drawer toggles,
  pool stacks under the outline, content readable.
- Objectives (`/<course>/objectives`): table scrolls, controls reachable.
- Calendar (`/<course>/calendar`): scrolls horizontally, weeks legible.
- Settings (`data.html`), Help, login: readable, no overflow.
- Desktop (>48rem): **unchanged** — the whole mobile block is gated behind the
  media query, so regressions should be impossible by construction; confirm
  anyway.
