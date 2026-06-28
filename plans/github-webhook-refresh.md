# GitHub webhook to refresh the deployed main view on push

## The problem

In collab mode (the Fly deployment), the shared read-only **main view** tracks
`origin/main`. When course content is changed *outside* the app and pushed to
GitHub, the deployment only learns about it by **polling**: a background timer
(`collab._start_main_timer` → `collab.refresh_main`) runs every
`main_refresh_seconds` (default 300s), fetches, and — if `origin/main` actually
moved — rebuilds the viewers' db (`rebuild_db(MAIN)`).

So an external push takes up to ~5 minutes to appear. We want it to appear
near-instantly by having GitHub **notify** the app on push, while keeping the
poll as a fallback.

## What already exists (and stays unchanged)

`collab.refresh_main()` (`collab.py`) is exactly the action a webhook needs:

1. `git fetch origin --prune`
2. `git checkout -B main origin/main` on the primary clone
3. if HEAD moved (or the MAIN db is missing), `rebuild_db(MAIN)` — a full
   `seed_module.load_courses` reload of every course from the freshly-pulled
   files.

It is already serialized under `_main_lock`, so a webhook call and a timer tick
can run concurrently without corrupting the clone or the db. **No change to the
refresh machinery is required** — the webhook just calls `refresh_main()`.

Two facts that shape the design:

- **Single worker.** The Fly app runs one gunicorn worker (it must — the timers,
  locks, and push queue are in-process state; see
  `plans/production-wsgi-server.md`). The webhook lands in the same process that
  owns `refresh_main` and `_main_lock`, so no cross-process coordination is
  needed.
- **`before_request` gate.** `_collab_gate` (`app.py`) redirects any non-exempt,
  unauthenticated request to the login page. GitHub is not a session user, so the
  webhook route must be added to `_AUTH_EXEMPT` (`app.py:176`).

## What this does NOT change

- **Editors still Sync manually.** Like the timer, the webhook only refreshes the
  shared *viewer* (`origin/main`) db. It does not merge `origin/main` into an
  individual editor's branch — that remains the manual **Sync** action
  (`POST /sync`). This feature makes the read-only view near-instant; it does not
  change editing.
- **The poll timer stays.** Webhooks can be missed (delivery failure, or the app
  was down during the push). The webhook *augments* polling; it does not replace
  it. Self-healing is the timer's job.

## Plan

### (1) Webhook endpoint — `app.py`

Add `POST /github/webhook`:

- **Gate to collab + configured secret.** If `not collab.enabled()` or
  `LESSON_GITHUB_WEBHOOK_SECRET` is unset, `abort(404)` — the route is inert in
  single-user/local mode and when no secret is configured.
- **Verify the signature.** GitHub signs the raw request body with HMAC-SHA256
  using the shared secret and sends `X-Hub-Signature-256: sha256=<hex>`. Compute
  `hmac.new(secret, request.get_data(), sha256)` and compare with
  `hmac.compare_digest` (constant-time). `abort(401)` on mismatch. This is the
  whole security model — without it anyone could POST to force rebuilds (a cheap
  DoS).
- **Filter to pushes on main.** Only act when `X-GitHub-Event: push` and the JSON
  payload's `ref == "refs/heads/main"`. Other events / branches → ack and ignore.
- **Refresh off-thread, ack immediately.** GitHub times out deliveries at ~10s,
  and a full `rebuild_db(MAIN)` of every course can approach that. Spawn a daemon
  thread running `collab.refresh_main` and return `("", 202)` right away.
  `_main_lock` already serializes the refresh against the timer, so overlapping
  deliveries are safe (and a redundant fetch with no new commits is cheap — no
  rebuild unless HEAD moved).

Sketch:

```python
import hmac, hashlib   # add to the imports at the top of app.py

@app.route("/github/webhook", methods=["POST"])
def github_webhook():
    secret = os.environ.get("LESSON_GITHUB_WEBHOOK_SECRET")
    if not (collab.enabled() and secret):
        abort(404)
    mac = "sha256=" + hmac.new(secret.encode(), request.get_data(),
                               hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, request.headers.get("X-Hub-Signature-256", "")):
        abort(401)
    if request.headers.get("X-GitHub-Event") == "push" and \
       (request.get_json(silent=True) or {}).get("ref") == "refs/heads/main":
        threading.Thread(target=collab.refresh_main, daemon=True,
                         name="github-webhook-refresh").start()
    return ("", 202)
```

### (2) Exempt it from the auth gate — `app.py`

Add `"github_webhook"` to `_AUTH_EXEMPT` (`app.py:176`) so `_collab_gate` lets the
unauthenticated POST through. (The endpoint does its own signature-based auth.)

### (3) Relax the poll interval (optional)

Once the webhook is the primary trigger, the timer is just a safety net. Consider
raising `main_refresh_seconds` from 300 to ~1800 (30 min) in the collab config so
the deployment isn't fetching every 5 minutes for the rare missed delivery. Left
as a config change, not code.

### (4) Configuration

- **Fly secret:** `fly secrets set LESSON_GITHUB_WEBHOOK_SECRET=<random>` (a long
  random string). Setting it triggers a redeploy, which is fine.
- **GitHub:** repo → Settings → Webhooks → Add webhook:
  - Payload URL: `https://<app>.fly.dev/github/webhook`
  - Content type: `application/json`
  - Secret: the same value
  - Events: "Just the push event"
- Document both `LESSON_GITHUB_WEBHOOK_SECRET` and the GitHub setup in
  `CLAUDE.md`/`README.md` next to the existing collab/`main_refresh_seconds`
  notes.

## Testing

- **Unit:** POST to `/github/webhook` with (a) a valid signature + a
  `refs/heads/main` push payload → `202` and `refresh_main` invoked; (b) a bad
  signature → `401`; (c) a `refs/heads/<other>` push → `202`, no refresh; (d)
  no secret configured → `404`. Stub `collab.refresh_main` / the thread to assert
  it's called rather than doing real git.
- **Manual on Fly:** push a trivial course change to `main`, confirm the viewer
  db reflects it within seconds (not minutes); check GitHub's webhook
  "Recent Deliveries" tab shows a `202`.

## Out of scope

- Pulling main into editor branches automatically (still manual Sync).
- Per-course incremental refresh (`refresh_main` rebuilds the whole MAIN db; it's
  already gated on HEAD actually moving, so the cost is paid only on real pushes).
- Any single-user/local behavior — the route is inert there by design.
