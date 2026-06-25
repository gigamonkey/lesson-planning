# Git-backed collaboration (deploy for teachers, merge via GitHub)

A deployment design that lets other teachers **view and edit** courses and
collaborate **asynchronously through normal GitHub mechanisms** (branches +
pull requests), rather than the live, character-level collaboration of
`multi-user-collaboration.md`. The two plans are alternatives:

- **`multi-user-collaboration.md`** — low-latency, several teachers on one
  outline at once, presence, an op-log. Lots of new realtime machinery.
- **This plan** — each teacher works in their **own git sandbox**; changes are
  shared by pushing a branch and opening a PR. Coarse-grained, no realtime
  infra, durable attribution. Good when teachers edit occasionally and review
  each other's work the way they'd review code.

The headline idea: move the corpus into **its own git repo**, deploy this app on
**fly.io** behind **GitHub OAuth**, and give each logged-in teacher a private
working copy that auto-commits and pushes to a branch named for their GitHub
handle. Merging happens on GitHub.

## The central correction: isolate the *database*, not just the worktree

The naive version of this plan ("each user saves into a git worktree named for
their handle") **does not work as stated**, and understanding why drives the
whole design.

The app is built around **one global SQLite db and one corpus dir**:

- `DB_PATH` and `CORPUS_DIR` are module-level globals (`app.py`).
- `db()` opens that single `DB_PATH`.
- All editing mutates **db rows**. The filesystem is touched only at *save*
  time, when `plan_io.write_course(DB_PATH, course, course_dir)` serializes the
  db back to `plan.md` + the two TSVs.

So the unit of concurrency is the **shared db**, not the filesystem. If two
teachers edit the same course, they stomp each other's db rows no matter which
worktree their saves land in. Worse, when teacher A hits save, `write_course`
dumps the *current global db* — which already contains B's edits — into A's
worktree. A's branch would then contain B's changes, mis-attributed. Per-user
worktrees alone buy **nothing**.

**The fix:** each logged-in teacher gets their own **(db, worktree) pair**,
resolved per-request from their identity. This turns the deployment into N
independent single-user instances of the app that happen to share one Flask
process — which is exactly the "everyone has their own sandbox, merge through
git" model the plan wants. Concretely:

- `DB_PATH` and `CORPUS_DIR` stop being module globals and become
  **per-request, per-user** values (see "Request-scoped corpus binding").
- A teacher's db is a **cache rebuilt from their worktree**, so it need not be
  durable; only the git repo must persist.

Everything below assumes this correction.

## Repository split

Today `courses/` is the default corpus and `examples/` is a sample corpus; the
app already reads `LESSON_CORPUS_DIR`. Step one is to stop committing course
content into *this* repo:

- Create a separate repo, e.g. `lesson-courses`, whose top level is a corpus
  directory (one subdir per course, each with `plan.md`, `*.md` reference
  hierarchies, `objectives.tsv`, `coverage.tsv` — the `plan_io` layout).
- Keep `examples/` here as the format demo / test fixture. Drop or empty
  `courses/` in this repo (it already ships empty).
- The app no longer points `LESSON_CORPUS_DIR` at an in-repo path; it points at
  a **clone of `lesson-courses`** on the fly volume.

This keeps "the engine" (this repo) and "the content" (the courses repo)
versioned independently, which is the whole point — teachers collaborate on
*content* without touching engine code.

## Identity: GitHub OAuth for login, a bot for push

These are **two different credentials** and conflating them is a trap:

- **GitHub OAuth (login).** Authenticates the teacher *to the app* and yields
  their GitHub **handle**, **name**, and **email**. Use a minimal scope
  (`read:user`, `user:email`). Set a signed session cookie; replace the
  `app.secret_key = "lesson-planning-dev"` placeholder with a real secret from
  fly secrets. The handle is the key for everything per-user (db path, worktree,
  branch). GitHub handles are already filesystem/branch-safe (alphanumeric +
  hyphen), but sanitize defensively anyway.

- **Push credential (write to GitHub).** OAuth login does **not** grant the app
  permission to push to the courses repo as the user. Don't try to push as each
  user with their token (needs `repo` scope + token storage + each user having
  write access). Instead the **app pushes with a single bot identity**. The
  *pusher* is the bot; the *commit author* is stamped as the teacher (`git -c
  user.name=… -c user.email=… commit`), so `git log` / GitHub still attribute
  the change correctly and PRs show the right person.

  **Decision: use an SSH deploy key.** Among the bot options:

  - **Deploy key (chosen).** One SSH keypair with **write** access to exactly
    one repo. Generate the key, add the public half as a deploy key (write
    enabled) on `lesson-courses`, store the private half as a fly secret, and the
    app pushes over SSH. No token rotation, no expiry, no extra GitHub account —
    minimal moving parts, scoped to one repo, which is the whole need here.
  - **GitHub App installation token** — the "more correct" option (short-lived
    tokens, finer scopes), but it adds JWT-signing + token-refresh machinery for
    no real benefit at single-machine / single-repo scale. Keep it as the upgrade
    path if this ever grows beyond one repo or needs auditable per-action tokens.
  - **Machine-user PAT** — rejected: a whole extra GitHub account to manage, and
    a PAT is easy to over-scope.

  If the deploy key leaks, the blast radius is write to this one content repo;
  rotate by swapping the key. That's an acceptable posture for this deployment.

### Access control: a static allowlist with roles

Gate login to a **static allowlist** (a config file / fly secret), where each
entry carries a **role**:

```
mrjones    editor
mslee      editor
guestprof  viewer
```

- **editor** — gets a per-user `(db, worktree, branch)` sandbox and can save.
- **viewer** — read-only; sees the canonical `main` state, no sandbox, edit
  endpoints disabled (see "Viewer mode").

There's no anonymous access. This is what lets us **deploy with just me
(`peter`) as the only editor** and everyone else as a viewer until the kinks are
out — adding editors later is a one-line allowlist change.

## Per-user git layout on the fly volume

One clone, many worktrees, on a persistent **fly volume** (e.g. mounted at
`/data`):

```
/data/courses.git/                  # bare-ish clone of lesson-courses (origin -> GitHub)
/data/worktrees/<handle>/           # git worktree, branch <handle>, tracks origin/<handle>
/data/db/<handle>.sqlite            # per-user db cache (rebuilt from the worktree)
```

Per-user bootstrap (first login, or when the worktree is missing):

1. `git fetch origin`.
2. If `origin/<handle>` exists, create the worktree from it; else create branch
   `<handle>` from `origin/main` and `git worktree add` it.
3. Build `/data/db/<handle>.sqlite` by seeding from the worktree corpus
   (`seed.load_corpus`), exactly like local startup does today.

Why worktrees (vs. N full clones): they share one object store, so disk and
fetch cost stay flat as teachers are added, and a single `git fetch` updates the
refs all worktrees branch from. Each user's worktree is on its own branch, so
the "can't check out the same branch twice" worktree rule is never hit.

## Request-scoped corpus binding (the main code change)

`DB_PATH` and `CORPUS_DIR` must stop being import-time globals and become
functions of the logged-in user. The cleanest seam, given the code:

- Add a tiny `session.py`/context helper: `current_user()` (from the OAuth
  session) and `user_paths(handle) -> (db_path, corpus_dir)`.
- Replace the module globals with a request-scoped accessor. Either:
  - a Flask `g`-based shim where `db()` reads `g.db_path` (set in a
    `before_request` that resolves the user and lazily bootstraps their
    sandbox), and a `corpus_dir()` helper replaces every `CORPUS_DIR` use; or
  - thread `(db_path, course_dir)` through the handful of call sites.

**Every** disk seam must use the per-user dir, not just `write_course`. Audit
shows these touch the corpus and must all bind per-user:

- `write_course` — `outline_source` (`app.py` ~1040), `export` (~1181).
- `read_course` / refresh (~1221).
- Hierarchy markdown upload writes `<slug>.md` into the corpus (~477–479).
- `is_dirty` / `has_corpus` in the sidebar nav (~200) and `savebtn` (~1200).
- Startup `seed`/`load_corpus` (~1539) becomes per-user lazy bootstrap.

A grep for `CORPUS_DIR` and `DB_PATH` enumerates the full set; the change is
mechanical once the accessor exists.

## The commit / push lifecycle

Saving already has one chokepoint — `write_course` — so commit+push hangs off
it. Make it a function `commit_and_push(handle, message)` called after every
successful `write_course` (the `outline_source`, `export`, and any future
disk-writing path) and after a hierarchy upload:

1. `write_course` writes `plan.md` + TSVs into `/data/worktrees/<handle>/…`
   (already the behavior, just per-user now).
2. `git -C <worktree> add -A`.
3. If nothing staged, stop (no empty commits).
4. `git -C <worktree> -c user.name="<Name>" -c user.email="<email>" commit -m
   "<message>"` — author = the teacher, committer = bot.
5. **Push best-effort**, not blocking the response: enqueue a push (background
   thread / simple in-process queue keyed by handle, coalescing rapid saves).
   Single-writer-per-branch means the push to `origin/<handle>` is a
   fast-forward and rarely fails.

Failure handling — the reason commit and push are **separate** steps:

- The commit is **always** local on the volume, so a teacher never loses work to
  a network blip.
- A failed push is retried on a backoff and re-attempted on the next save. If it
  keeps failing (e.g. someone rebased `<handle>` on GitHub), surface a banner:
  "couldn't sync to GitHub — N local commits pending" with a manual retry. Don't
  auto-`--force`.

### Commit granularity and messages: one commit per save, message from collected actions

**Decision: one commit per explicit save** (no debouncing across saves). Simple,
predictable, and a save is already the natural "I'm done with this batch" beat.

The wrinkle: most editing in the structured workspace mutates the **db** and
never hits disk — `write_course` (and thus a commit) only fires on an explicit
save/export. So between saves a teacher may have done a dozen things (renamed a
unit, placed objectives, added a lesson). Rather than a generic "Edit Calc
outline" message, **collect action descriptions as they happen** and roll them
into the save's commit message:

- Maintain a small **per-user pending-actions buffer** (in the session / a tiny
  table keyed by handle). Each mutation endpoint appends one short human phrase
  as it runs — e.g. `place` → "placed 3 objectives in Lesson 2.1", `unit_rename`
  → "renamed unit *Sorting* → *Searching*", `lesson_new` → "added lesson to
  Unit 4". (These are the same endpoints the op-log taxonomy in
  `multi-user-collaboration.md` enumerates — reuse those human descriptions.)
- On save, `commit_and_push` **consumes and clears** the buffer, composing the
  message: a one-line summary plus the collected phrases as a bullet body, e.g.

  ```
  Edit Calc outline (4 changes)

  - renamed unit Sorting → Searching
  - placed 3 objectives in Lesson 2.1
  - added lesson to Unit 4
  - set Unit 4 duration to 2 weeks
  ```

- If the buffer is empty (e.g. a save right after a Markdown-editor edit, which
  is itself one wholesale action), fall back to a single derived phrase ("Edit
  Calc outline via Markdown", "Upload AB hierarchy", "Import objectives").

This keeps GitHub history legible — a reviewer reading the PR sees *what
changed* in plain English, not just "Edit outline".

## Staying current with `main` (don't let sandboxes rot)

Collaboration-through-git only works if teachers **see merged changes**. A
teacher's edits stream to `origin/<handle>`; eventually some of those land in
`main` (via PR). Those `main` changes must then flow back into **every** user's
worktree branch.

**Decision: merge `origin/main` into each worktree branch — never rebase.** The
worktree branch *is* the published `origin/<handle>`, so rebasing it would
rewrite already-pushed history and force a `--force` push (which can clobber and
breaks the "single-writer, always fast-forward" property that makes pushes safe).
A merge only *adds* a merge commit, so the subsequent push to `origin/<handle>`
stays a plain fast-forward. Concretely:

- On **session start** (and a "Sync" button), `git fetch origin`, then
  `git -C <worktree> merge origin/main` on the user's branch. On conflict, show
  the conflicted files; because the corpus is small and human-readable, conflicts
  are rare and legible. A no-op when `main` hasn't moved.
- After any merge that changed files, **rebuild the user's db** from the worktree
  (`read_course`/reseed) so the running app reflects the merged corpus.
- Document the loop for teachers: edit → auto-pushed to `<handle>` → open a PR to
  `main` on GitHub → review/merge → others pick it up via Sync.

**PR merge-strategy caveat.** This back-merge is cleanest when PRs are merged
with a **merge commit** (the default), because then `main` literally contains the
branch's commits and merging `main` back into `<handle>` fast-forwards with
nothing to reconcile. If PRs are **squash-merged**, `main` gets one new commit
that isn't an ancestor of the branch's individual commits, so the back-merge can
report spurious conflicts on lines the branch already changed. So: **prefer
merge-commit PRs** for the courses repo. (If a teacher's whole branch has landed
and their sandbox is otherwise clean, an alternative is to reset their branch to
`origin/main` and re-create the worktree fresh — simpler than untangling a
squash, and fine because nothing local is unpushed.)

A periodic job can also merge `origin/main` into every clean (no-unpushed-work)
user branch and push, so idle sandboxes don't silently rot between sessions.

## Merge quality of the corpus files

The plan's premise — readable files → manageable diffs — is mostly true, with
caveats to design around:

- `plan.md` diffs are excellent (prose + structure).
- `objectives.tsv` / `coverage.tsv` use abbreviated-uuid tokens and **position**
  columns. Concurrent reorders can merge **textually clean but semantically
  off** (duplicate/!gapless positions). Mitigation: after any merge or pull,
  **reload → re-export** so `write_course` re-normalizes ordering and TSV shape;
  a tiny validator can flag duplicate positions before commit.
- Stable token identity (already uuid-based) keeps most edits as line-local
  diffs rather than whole-file churn — keep it that way (don't renumber on
  unrelated edits).

## fly.io deployment specifics

- **One machine, one volume.** State persistence (the git clone, worktrees,
  per-user dbs) lives on a fly **volume**; volumes bind to a single machine, so
  run **exactly one** machine. This scale (a handful of teachers) is well within
  one small machine + SQLite.
- **Ephemeral rootfs.** Anything not on the volume is lost on restart/redeploy.
  Only the **git repo** must survive; per-user dbs are caches and can be rebuilt
  from worktrees on demand, so they *can* live on the volume but don't have to.
- **Secrets** as fly secrets: OAuth client id/secret, Flask `secret_key`, the
  **SSH deploy key** private half (and the allowlist, if not committed).
- **Boot:** ensure `/data/courses.git` exists (clone on first boot), `git
  fetch`, then serve. Per-user sandboxes are created lazily on that user's first
  request.
- **Scale-to-zero** is fine: on wake, the volume still holds the repo; pending
  pushes (if any survived as local commits) re-attempt on the next save/sync.
- Listen on `0.0.0.0` (already the yolo behavior) and the fly-mapped port.

## Viewer mode (first-class, built in from the start)

"Look at it" is a primary use case, and viewer mode is also what lets us **deploy
early and test with just `peter` as editor** while everyone else watches. So
it's built in, not deferred.

A **viewer** (per the allowlist role) is bound to the canonical `main` state, not
a personal sandbox:

- One shared, read-only **"main" db**, seeded from a single `main` worktree
  (`/data/worktrees/_main`, kept on `origin/main`). All viewers share it.
- After the sync job advances `_main` to a new `origin/main`, rebuild the shared
  db once; all viewers see the update.
- **Edit endpoints are disabled** for viewers — guard the mutation routes (and
  hide their UI affordances: save, drag, add/delete, the "Edit as Markdown"
  button) behind an `is_editor` check. A viewer hitting a mutation route gets a
  clean 403, not a half-applied edit.
- No worktree, no branch, no commit/push path for viewers — they're pure readers
  of `main`.

The role check is a single helper (`current_user().role`) consulted in
`before_request` to pick the binding: editor → `(personal db, personal
worktree)`; viewer → `(shared main db, read-only)`.

## What this deliberately does *not* do

- No realtime/presence/op-log (that's `multi-user-collaboration.md`). Two
  teachers editing the *same* course at the *same* time don't see each other
  live; they reconcile via PRs. Within a single teacher's sandbox, concurrent
  tabs are last-write-wins, exactly like the app today.
- No per-field merge — conflict resolution is git's, on whole files.

## Phased rollout

1. **Repo split.** Create `lesson-courses`; move content out of this repo; point
   `LESSON_CORPUS_DIR` at a clone. Verify the app runs unchanged against it
   (still single global db — no behavior change yet).
2. **Auth + roles.** GitHub OAuth login, signed sessions, real `secret_key`, the
   static allowlist with editor/viewer roles. No per-user state yet — everyone
   shares the global sandbox, but now identified and role-gated. Ship to confirm
   login works.
3. **Viewer mode.** Shared read-only `main` db + worktree; `is_editor` guards on
   all mutation routes and UI affordances. Now the app is safe to expose: viewers
   see `main`, nobody-but-editors can write. **Deploy here with `peter` as the
   only editor** and shake out the rest behind real use.
4. **Per-user sandboxes (the core change).** Request-scoped `(db_path,
   corpus_dir)`; lazy bootstrap of worktree + db per editor handle; audit every
   `CORPUS_DIR`/`DB_PATH` seam. Now each editor works in isolation.
5. **Commit + push.** Per-user pending-actions buffer feeding commit messages;
   `commit_and_push` after every save/upload; author-stamped commits; deploy-key
   push over SSH in a background queue with retry + pending-sync banner.
6. **Sync with main.** Session-start fetch + `merge origin/main`, "Sync" button,
   db rebuild after merge, conflict surfacing. Document the PR workflow for
   teachers; set the courses repo to merge-commit PRs.
7. **fly.io.** Volume, one machine, secrets, boot clone/fetch, deploy key for
   push. Promote more allowlist entries to `editor` as things stabilize.
8. **Polish.** Periodic back-merge of `main` into clean branches; TSV
   normalize-on-merge validator; commit-message tuning.

## Decisions — resolved

- **Push identity:** SSH **deploy key** with write to the one courses repo
  (GitHub App held as the upgrade path).
- **Access control:** **static allowlist**, each entry tagged `editor` or
  `viewer`.
- **Commit granularity:** **one commit per explicit save**, message composed from
  a per-user buffer of action descriptions collected as edits happen.
- **Sync strategy:** **merge** `origin/main` into worktree branches (never
  rebase — avoids rewriting published history / force-push); courses repo uses
  merge-commit PRs so the back-merge stays clean.
- **Viewer mode:** **built in from the start**, bound to a shared read-only
  `main`; enables an early `peter`-only-editor deploy.

## Decisions — resolved (continued)

- **PR creation: self-serve.** Teachers open PRs themselves on GitHub from their
  `<handle>` branch; the app does not create PRs. (A "propose for review" button
  that opens the PR via the API is a possible later convenience, not v1.) The app
  can still link out to GitHub's compare/PR page for the user's branch to make
  the next step obvious.
- **Viewer scope: `main` only.** Viewers see the canonical `main` state and
  nothing else — no peeking at editors' in-progress branches. Keeps viewer mode a
  single shared read-only binding.
