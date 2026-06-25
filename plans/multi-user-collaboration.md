# Multi-user collaboration on outlines

Let several teachers work on a course outline **at the same time**, with changes
showing up in each other's browsers at low latency, and with a durable record of
**who changed what, when**. The expected scale is small — a handful of teachers
on one outline at a time — so the design optimizes for *simplicity and
attribution*, not for thousands of concurrent editors.

The user named **Operational Transformation (OT)** and **CRDTs** as candidate
techniques. This plan argues that for the way this app is actually built, the
right answer is mostly **neither** — a central, server-ordered **operation log**
gives us everything we need with far less machinery — and that a text CRDT is
worth considering for exactly one corner of the app (the free-text Markdown
editor). The reasoning is below.

## The key realization: two very different editing surfaces

The outline is edited two ways today, and they have opposite shapes:

1. **The structured workspace** (`/<course>/h/<hierarchy>`, `workspace.html`).
   Drag-and-drop (SortableJS), inline rename, add/delete unit & lesson, set
   durations, bulk-edit a node's objectives. Each interaction is already a
   **small, semantic operation** POSTed to a dedicated endpoint — `place`,
   `node_objectives_bulk`, `unit_new`, `unit_rename`, `lesson_edit`,
   `lesson_arrange`, `node_duration_set`, etc. This is the *primary* surface.

2. **The Markdown editor** (`/<course>/outline/edit`, CodeMirror 6). The whole
   `plan.md` is edited as free text and POSTed back in one blob; the server runs
   `plan_io.load_plan_text`, which **deletes and rebuilds the entire outline and
   pool** from the buffer. This is a wholesale replace.

These need different treatments. The structured surface is a stream of
operations on a small shared tree — perfect for an **operation log**. The
Markdown buffer is exactly the Google-Docs-style free-text problem that OT and
CRDTs were invented for — but it's the secondary surface, and (critically) its
"save" is destructive, which is incompatible with concurrent editing as written.

## Why not full OT or CRDTs (for the structured model)

OT and CRDTs solve **concurrent convergence without a central authority** — peers
exchange edits and each must independently arrive at the same state. Their
complexity (transformation functions for OT; tombstones, vector clocks, and
careful commutativity for CRDTs) is the price of *not* having a single point that
decides ordering.

This app **does** have that single point: one Flask server in front of one SQLite
database. We can make the server **authoritative** and let it **totally order**
every operation (SQLite's write serialization is the ordering point). With a
central authority and only a few online clients, we don't need transform
functions or CRDT merge math — we need:

- a server that validates and applies operations one at a time;
- per-operation **conflict rules** that are obvious at the domain level
  (last-writer-wins on a field; set semantics for placement; renumber positions
  on insert);
- **optimistic** client updates that reconcile against the server's broadcast.

This is sometimes called *server-authoritative operational sync*. It is far
simpler than OT/CRDT and is the standard choice when you control a central
server and concurrency is low. It also maps cleanly onto the existing
endpoint-per-operation design — we're formalizing what the app already does.

**Where a CRDT still earns its keep:** the free-text Markdown editor (see
"The Markdown editor problem"). That's genuine character-level concurrent text;
if we want true simultaneous prose editing there, a text CRDT (e.g. Yjs +
`y-codemirror`) is the pragmatic tool. It's optional and isolatable.

## Recommended architecture

### 1. Identity (who)

There is no notion of a user today (`app.secret_key = "...not security-sensitive"`).
Add the minimum needed for attribution:

- A `users` table (`id`, `name`, `email`, optional `color` for presence).
- A lightweight sign-in: for a trusted handful of teachers, a name/email picker
  that sets a signed session cookie is enough; we can upgrade to real auth later.
  (If the app is exposed beyond the LAN, put it behind SSO/reverse-proxy auth.)
- Every write carries the acting user's id (from the session).

### 2. An append-only operation log (the source of change history)

A new table makes operations first-class and gives us attribution + history for
free:

```sql
CREATE TABLE op_log (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,  -- global total order
  course     TEXT NOT NULL,
  hierarchy  TEXT NOT NULL,                       -- the outline being edited
  user_id    TEXT NOT NULL,
  ts         TEXT NOT NULL,                        -- ISO-8601 UTC
  kind       TEXT NOT NULL,                        -- 'place' | 'unit_rename' | ...
  payload    TEXT NOT NULL,                        -- JSON: the op's args
  inverse    TEXT                                  -- JSON: enough to undo (optional)
);
```

- The server applies every mutation as: **validate → mutate the relational
  tables (as today) → append one `op_log` row → broadcast** (all in one SQLite
  transaction so the log can never disagree with the state).
- `seq` is the authoritative total order. Clients track the last `seq` they've
  seen so they can request "everything since N" after a reconnect.
- The log **is** the audit trail ("who changed what"): no separate history
  mechanism is needed, and it doubles as the basis for undo/redo and revert.

The existing endpoints become thin wrappers that build a typed op and hand it to
a single `apply_op(conn, op)` function, so the CLI, the workspace, and replay all
go through one path (mirroring how the app already wires to library functions
rather than reimplementing them).

### 3. Operation taxonomy

Map the current endpoints to named ops (this is mostly relabeling existing code):

| Op kind            | From endpoint            | Conflict rule                                   |
|--------------------|--------------------------|-------------------------------------------------|
| `place`            | `place`                  | set membership; renumber `coverage.position`    |
| `node_objectives`  | `node_objectives_bulk`   | LWW on the node's objective set (see note)      |
| `unit_new`         | `unit_new`               | append; commutative                             |
| `unit_rename`      | `unit_rename`            | LWW on `nodes.text`                             |
| `unit_delete`      | `unit_delete`            | tombstone (don't hard-delete; see undo)         |
| `unit_move`        | `unit_move`              | LWW on `ordinal`; renumber siblings             |
| `lesson_new`       | `lesson_new`             | append                                          |
| `lesson_edit`      | `lesson_edit`            | LWW per field (title / learning_objective)       |
| `lesson_delete`    | `lesson_delete`          | tombstone                                       |
| `lesson_arrange`   | `lesson_arrange`         | LWW on the arranged order                       |
| `duration_set`     | `node_duration_set`      | LWW                                            |
| `objective_edit`   | `objective_edit`         | LWW on `objectives.text` (already text-keyed)   |

Most of these are naturally last-writer-wins on a small field, which is fine for
a few collaborators: the loser sees the winner's value appear a moment later via
the broadcast. The two that need care:

- **Position renumbering** (`place`, `*_arrange`, `*_move`). Concurrent reorders
  can interleave. Keep the current scheme (the client sends the full intended
  order of the affected zone), apply server-side, and broadcast the resulting
  order so everyone snaps to the same arrangement. With few editors this is
  smooth; if it proves jittery, switch `ordinal`/`position` to **fractional
  indexing** (insert between `a` and `b` by averaging their keys) so inserts
  don't renumber siblings and rarely collide.
- **`node_objectives_bulk`** currently replaces a node's whole objective list.
  Under concurrency, scope the op to the *delta* (added/removed/retained tokens)
  rather than "replace with exactly this set," so two people adding different
  objectives to the same lesson don't clobber each other.

### 4. Real-time transport: Server-Sent Events (SSE)

Use **SSE**, not WebSockets. The data flow is asymmetric — clients already send
mutations as ordinary POSTs (htmx), and only need a **server → client** push of
"here's what changed." SSE is one-directional, rides plain HTTP, reconnects
automatically, and needs no new protocol or infra — a clean fit for Flask + htmx.

- New endpoint `GET /<course>/h/<hierarchy>/stream` returns
  `text/event-stream`. The client opens an `EventSource` and includes its
  `Last-Event-ID` (the last `seq` it applied) so the server can replay any ops it
  missed before going live.
- After `apply_op` commits, the server publishes the op to an in-process
  pub/sub (a simple per-`(course, hierarchy)` fan-out of queues). Each connected
  `EventSource` for that outline receives the op as an event whose `id:` is the
  `seq`.
- This is single-process. Since the deployment is one small Flask app, that's
  fine. If we ever run multiple workers, back the pub/sub with SQLite polling on
  `op_log.seq` or a tiny Redis channel — but that's explicitly out of scope for
  "a few teachers."

### 5. Client: optimistic apply + reconcile + presence

- On a local edit, update the DOM immediately (optimistic), POST the op, and let
  the SSE broadcast confirm it. Because the same op comes back over the stream
  tagged with its `seq` and author, the client can dedupe its own ops by a
  client-generated `op_id` echoed in the broadcast.
- On receiving a remote op, apply it to the DOM. The ops are coarse enough
  (rename this node, move this lesson, place this objective) that a targeted DOM
  patch — or, simplest, re-fetching the affected fragment via htmx (we already
  return zone/stat fragments) — keeps the implementation small.
- **Presence/awareness**: a side channel (same SSE stream, different event type)
  carrying "user X is here / editing lesson Y." Render avatars/initials and a
  subtle highlight on the node someone else is editing. This is the visible
  "see who's working" payoff and is cheap to add once the stream exists.

### 6. The Markdown editor problem

`load_plan_text` does a **destructive full rebuild** of the outline and pool from
the submitted buffer. That is incompatible with concurrent editing: a teacher who
opens the editor, edits for five minutes, and saves would erase everyone else's
intervening structured edits. Options, in order of effort:

1. **Lease/lock (do this first).** Entering the Markdown editor takes a
   short-lived, auto-renewing **edit lease** on the outline. While held, the
   structured workspace shows "Pat is editing the source" and is read-only;
   others can't take the lease. On save or lease expiry, normal collaboration
   resumes. Simple, correct, and matches how rarely the raw editor is used.

   The lease is single-writer for the Markdown editor only — the structured
   workspace never needs one, since those are small per-field ops that merge
   through the op-log. The details that keep a lease from getting stuck:

   - **Storage.** A row per outline: `lease(hierarchy PK, user_id, acquired_ts,
     expires_ts)`. Acquire = insert-if-absent-or-expired; it's the serialization
     point, so two simultaneous "open editor" requests can't both win.
   - **Short TTL, not "until save."** The lease carries a TTL of ~60–120s.
     "Until they save" alone would freeze the outline forever if the editor
     closes their laptop, so the lease's lifetime is bounded by the TTL and kept
     alive only while the editor is actually present.
   - **Heartbeat renewal.** The open editor page renews the lease on a timer
     (well inside the TTL, e.g. every ~30s) — piggybacking on the existing
     CodeMirror change listener plus an idle timer. While heartbeats arrive,
     `expires_ts` is pushed forward and the holder keeps the lease.
   - **Expiry = auto-unlock.** If heartbeats stop (tab closed, crash, sleep), the
     lease lapses at `expires_ts` and the next person's acquire succeeds; the
     workspace re-enables itself on the next SSE tick. A `beforeunload` handler
     also releases the lease eagerly on a clean close, so normal exits unlock
     immediately rather than waiting out the TTL.
   - **Renewal vs. takeover.** A renew only succeeds for the current holder; once
     expired, the lease is free and anyone may take it. Optionally surface a
     "take over editing" action that force-expires a stale lease (with a warning),
     for the case where someone needs in and the holder is plainly gone.
   - **Lost-lease save.** If a save arrives after the lease expired and someone
     else has since edited, reject the destructive rebuild rather than clobber —
     show the user their buffer is stale and let them re-open against current
     state. (This is the seam where option (2) later removes the problem.)
2. **Diff-and-apply instead of replace.** Make saving compute the diff between
   the buffer's parsed model and the current db state and emit it as a *series of
   ops* (reusing the taxonomy above), rather than DELETE-and-rebuild. Then a
   Markdown save merges instead of clobbering, and it lands in the op-log with
   attribution like any other edit. More work, but removes the lock.
3. **Text CRDT (only if simultaneous prose editing is wanted).** Replace the
   CodeMirror buffer with a Yjs document via `y-codemirror`, synced over the same
   SSE/WS channel, with the server persisting and periodically reparsing into the
   db. This is where a CRDT genuinely fits — but it adds a CRDT runtime and a
   second source of truth (the Yjs doc vs. the db/markdown), so only pursue it if
   teachers actually want to co-type prose in the raw editor.

Recommendation: ship **(1)** with the first cut, consider **(2)** later, treat
**(3)** as opt-in polish.

### 7. Persistence, the corpus, and git

The markdown + TSV corpus stays the durable, git-tracked source of truth; the
op-log is the **live, in-session** layer on top of the db cache.

- Keep `write_course` / `is_dirty` / the save button. A save still serializes the
  db to `plan.md` + TSVs.
- **Attribute git commits**: when exporting/committing the corpus, set the git
  author to the user who saved (or, for a session with multiple contributors,
  list co-authors). The op-log gives finer-grained, real-time history *within* a
  session; git remains the durable archive *across* sessions. They complement
  each other rather than compete.
- Optionally, an auto-export/commit on a debounce (e.g. 30s after the last op, or
  on session end) so the corpus doesn't drift far from the live db — but keep it
  opt-in to avoid noisy history.

### 8. Concurrency & consistency details

- Put SQLite in **WAL mode** (`PRAGMA journal_mode=WAL`) so the SSE readers and
  the writer don't block each other; keep write transactions short. One small
  app with a few users is well within SQLite's comfort zone.
- All writes go through `apply_op` in a single transaction (mutate + log). Serialize
  writers with a process-level lock or a single writer connection/queue if needed;
  SQLite's own write lock is usually sufficient at this scale.
- Make ops **idempotent** where possible (keyed by `op_id`) so a client retry
  after a flaky POST doesn't double-apply.
- Reconnect protocol: client sends `Last-Event-ID = last seq`; server replays
  `op_log WHERE seq > N` then resumes live. A client far behind (e.g. laptop
  asleep) can just reload the page.

## History & change-tracking UI

The op-log makes this almost free:

- A per-course **activity feed**: `seq`, time, user, human-readable description
  ("Pat moved *Recursion* before *Sorting*", "Sam placed 3 objectives in
  *Lesson 2.1*"). Render from `op_log` rows.
- **Per-node attribution**: "last edited by Sam, 2h ago" badges, derived from the
  latest op touching that node.
- **Undo/redo and revert**: each op stores enough to invert it (`inverse`), so a
  user can undo their last op, or an admin can revert a specific change. (Full
  time-travel — reconstruct the outline at seq N — is possible by replaying the
  log from empty, but isn't needed for v1.)

## Phased rollout

1. **Identity + op-log foundation.** Add `users`, sign-in, and the `op_log`
   table. Refactor the existing mutation endpoints to go through `apply_op`
   (validate → mutate → log). No real-time yet; ship the **activity feed** and
   per-node "last edited by" as the first visible win. WAL mode on.
2. **Real-time push.** Add the SSE `/stream` endpoint + in-process pub/sub +
   client `EventSource`. Apply remote ops by re-fetching the affected fragments.
   Add **presence** (who's online, who's editing what).
3. **Markdown editor safety.** Add the edit **lease** so the raw editor can't
   clobber concurrent structured edits.
4. **Polish.** Optimistic-apply dedupe by `op_id`, undo/redo from `inverse`,
   fractional indexing if reorder jitter shows up, git-author attribution on
   export.
5. **Optional.** Diff-and-apply Markdown saves (drop the lease); Yjs text CRDT in
   the raw editor if co-typing prose is wanted.

## Decisions to confirm

- **Auth depth.** Is a trusted name/email picker enough, or does this need real
  authentication (the answer depends on whether it's exposed beyond the school
  LAN)?
- **Granularity of "track changes."** Is a per-op activity feed + per-node
  attribution sufficient, or do teachers want full document time-travel and
  diffs between arbitrary points?
- **Markdown editor importance.** How much do teachers actually use the raw
  Markdown editor concurrently? This decides whether we can live with a lease
  (cheap) or must invest in diff-and-apply / a text CRDT.
- **Deployment shape.** Single Flask process (assumed here, makes SSE trivial),
  or multiple workers (would need a shared pub/sub)?

## Why this over the alternatives (summary)

- **Full CRDT/OT everywhere** — rejected: solves decentralized convergence we
  don't have (one server, one db), at high complexity, and fights the existing
  "markdown/db is the source of truth, the outline is structured" design.
- **Server-authoritative op-log + SSE** — chosen: minimal new machinery, reuses
  the endpoint-per-operation design, gives attribution/history/undo for free, and
  delivers low-latency updates for the realistic scale (a few teachers).
- **Text CRDT, scoped to the Markdown editor** — held in reserve: the one place
  character-level concurrent text genuinely applies, but secondary and isolable.
