# Manual Save (decouple writing files from committing)

## Goal

Today every edit auto-persists to git: a debounced timer does `write_course`
**and** `commit_repo` (+ push) together; structural ops commit themselves
immediately. There is **no Save button**.

We want to **decouple writing files from committing**:

- **Files** stay continuously in sync with the db via the existing autosave
  timer, but the timer now only **writes files** — it never commits. The db
  remains a pure cache (fast reads, ad-hoc queries); the on-disk course files are
  the real storage. Drift between db and disk is at most the debounce window.
- **Commit** becomes the only explicit action: a **Save** button reifies the
  latest state, **prompts for a commit message**, commits, and — in collab mode —
  **pushes to GitHub immediately**. Local-git/demo just commits (no push).
- A `beforeunload` flush makes sure the most recent edit is on disk the moment you
  navigate away (belt-and-suspenders; the server-side timer already writes within
  the debounce window regardless).

Net effect: your *work* is always safe on disk (autosaved); a *commit* is a
checkpoint you choose to make, with a message you choose.

## Current state (what we're changing)

- `app.py`
  - `_autocommit_edit` (after_request): records an action phrase **and** schedules
    a debounced autosave whose `flush` does `write_course` + `commit_repo`
    (+ enqueue push).
  - `commit_structural(course, msg, …)`: writes/deletes files **and** commits
    immediately — the `_IMMEDIATE_OPS` (course new/delete/import, reference
    add/remove, objective uploads).
  - `commit_after_save` (outline_source/lesson_source): `write_course` already
    done by the route, then commit.
  - `sync_courses` (`/sync`): collab → write all + commit + merge origin/main +
    push; single-user → reload every course from disk.
  - Boot: single-user/local-git **rebuilds the db from disk every start**; collab
    builds each user's db lazily, persisted on the volume.
- `collab.py`: action buffer (`record_action`/`compose_message`), `commit_repo`
  (stage-add-commit-maybe-push), `schedule_autosave`/`_autosave_fire`/
  `cancel_autosave`, push worker, `sync`, `push_status`.
- `templates/base.html`: a **Sync** button and a polled push-status banner; no
  Save button.

## Proposed model

### Lifecycle

1. **Edit → db.** Mutating routes update the db as today.
2. **Autosave timer → files only.** The `flush` becomes `write_course(course)`
   with **no** commit/push. So disk converges to db within the debounce window.
   (`schedule_autosave`, the timer machinery, `LESSON_AUTOSAVE_SECONDS` all stay.)
3. **Structural ops → files now, no commit.** `course_delete` still `rmtree`s,
   `hierarchy_delete` still removes the reference file, etc. — disk reconciliations
   `write_course` can't express, done eagerly so Save's `git add -A` sweeps them
   up. They just don't commit.
4. **Save** (`POST /save`, new):
   - Flush synchronously first: cancel the pending timer, `write_course` every db
     course (so the very latest edit is on disk).
   - `git add -A`; if nothing changed, "Nothing to save."
   - Else commit with the **user-supplied message**; collab **pushes
     synchronously** and the flash reports the result; local-git stops at commit.
5. **`beforeunload` → flush.** `navigator.sendBeacon('/flush')` forces an
   immediate `write_course` of the dirty courses, so disk is current on exit.

### Commit-message suggestion

Keep `record_action`; the after_request hook still appends a phrase per edit. The
Save dialog pre-fills with `compose_message(key, "Update courses")`; Save consumes
the buffer.

### Uncommitted indicator

A small in-process flag keyed by the autosave key (`g.handle` / `"_local"`): set
on any mutating POST + structural op, cleared on a successful Save. Drives the
Save button's highlighted/"• unsaved" state. `git status --porcelain` is the
authoritative check used **at Save/Sync time** (no-op detection, Sync guard); the
flag is just the cheap UI hint. (Alternative: drop the flag and poll
`git status` — more accurate re: external edits, but a subprocess per poll.)

## Sync under this model

- **Collab Sync** merges `origin/main` into the branch, which needs a clean tree.
  Since we no longer auto-commit, autosaved-but-uncommitted files make the tree
  dirty → Sync should **flush, then require a commit**: if `git status` is dirty,
  flash "Save your changes before syncing." (No more auto-commit-with-generated-
  message inside Sync.)
- **Single-user Sync** reloads the db from disk. Because the autosave timer keeps
  files == db, this reload is effectively lossless even with uncommitted changes
  (the "uncommitted" work is on disk and reloads right back). It keeps its role of
  picking up external edits / a manual `git pull`. No dirty guard needed.

## UI

- **Save button** in the sidebar `collabbar`, both modes (when `can_edit`).
  Greyed when clean; highlighted with a "• unsaved" hint when dirty.
- Clicking Save opens a **commit-message dialog** (`<dialog>`, like help /
  new-course): a text input pre-filled with the suggested message + Save / Cancel.
  Submits to `/save`.
- Flash the result after Save (subject; pushed / push error in collab; "Nothing
  to save" when clean).
- `beforeunload` handler sendBeacons `/flush` (only when dirty).

## Changes by file (sketch)

- `app.py`
  - `_autocommit_edit`: keep `record_action` + set the dirty flag; change the
    scheduled `flush` to `write_course` only (drop `commit_repo`/push).
  - `commit_structural` → `apply_structural`: file mutation + set dirty; **no
    commit**. Update its ~9 call sites.
  - `commit_after_save`: just set dirty (route already wrote files).
  - New `save_courses` (`POST /save`): lifecycle step 4; synchronous push in
    collab (reuse `commit_repo(push_key=None)` then `collab._push_once`, or a thin
    helper). Reads message from the form; clears the dirty flag + buffer.
  - New `flush_courses` (`POST /flush`): write dirty courses now (for the beacon);
    returns 204.
  - `sync_courses`: drop the "auto-commit everything" step; add the dirty guard
    for collab; keep single-user reload.
  - `inject_collab`: expose `dirty` + `save_suggestion`; update comments.
  - Boot: **unchanged** — rebuild-on-boot is fine now that files track the db.
- `collab.py`: keep everything; `sync` no longer needs the pre-commit step
  (caller guards instead). `cancel_autosave` now used by Save/flush. Possibly add
  `commit_and_push_sync`.
- `templates/`: `_save_dialog.html` (new), Save button + dirty hint in
  `base.html`, rework `_collab_pending.html` polling, `beforeunload` JS.
- Docs: flip the `CLAUDE.md` "no manual Save button" note back; note that the
  timer now writes files only and commit is explicit.
- Tests: add a check that `/save` writes + commits (no-op when clean) and that the
  autosave flush writes files without committing.

## Open questions / decisions

1. **Save scope = all courses, one message.** Proposed yes (`git add -A`, single
   message). Matches "reify everything."

2. **Uncommitted indicator: app flag vs. live `git status`.** Proposed: app flag
   for the cheap UI hint, `git status` as truth at Save/Sync. OK, or prefer
   polling `git status` so external/manual edits also show as "unsaved"?

3. **Collab Sync while dirty:** refuse with "Save first" (proposed) vs. let Sync
   prompt for a message and commit as part of syncing. Proposed: refuse.

4. **`beforeunload`:** flush-only (proposed), or also a "you have uncommitted
   changes" confirm? Since work is safe on disk, a confirm seems unnecessary —
   flush is enough.

5. **Push synchronous in Save** (proposed) so the flash reports the real result;
   keep the async worker for retry on failure.

6. **Default commit message** from the action-phrase summary (proposed) vs. blank
   with a placeholder.

7. **Keep `LESSON_AUTOSAVE_SECONDS`** (now "file autosave debounce", not commit).
   Proposed: keep, maybe document the changed meaning.

## Out of scope

- Conflict resolution UI (still "resolve on GitHub").
- Per-course save buttons (one global Save).
- On-disk format / db schema changes.
