# Production WSGI server for the fly deploy

## Problem

The fly deployment runs the app with `CMD ["uv", "run", "app.py"]`, which calls
Flask's `app.run()` — Werkzeug's **development** server. On every boot the logs
carry:

> WARNING: This is a development server. Do not use it in a production
> deployment. Use a production WSGI server instead.

We want to serve behind a real WSGI server and silence the warning, without
breaking the collaboration deployment's correctness assumptions.

## The hard constraint: one process, threads not workers

This app is **not** a stateless request/response service. A pile of live state
sits in process memory and only works if there is exactly **one** process
serving:

- the background **push queue** + worker thread (`collab.py:413`, `:657`)
- the **refresh/autosave timers** (`threading.Timer`, `collab.py:456`, `:498`,
  `:675`)
- the per-handle **db locks**, **commit lock**, **main lock**, **action
  buffers** (`collab.py:216`, `:281`, `:317`, `:347`, `:477`)
- the per-handle **SQLite caches** and the single **git clone** on the volume

`fly.toml` already documents this: *"Single machine only… Do NOT scale to >1
machine."* The same logic applies within the machine: **do not run multiple
worker processes.** Concurrency must come from **threads**, which all share the
one process's memory and timers.

There is a second, subtler trap: **`collab.startup()` runs at module import
time** (`app.py:32`, outside the `if __name__ == "__main__"` guard), spawning the
push worker thread and timers. If a WSGI server imports the app in a **master**
process and then **forks** workers (gunicorn `--preload`), those threads/timers
live in the master and **die on fork** — the workers would serve requests with a
dead pusher. So: **no `--preload`, exactly one worker**, and let the import (and
thus `startup()`) happen inside that single worker.

The single-user mode (`ensure_schema()` + `seed`) has the same import-time
behavior but no threads, so it is unaffected either way.

## Decision: gunicorn, 1 gthread worker

Use **gunicorn** with a single threaded worker:

```
gunicorn --workers 1 --threads 8 --worker-class gthread \
         --bind 0.0.0.0:8080 --timeout 120 app:app
```

- `--workers 1` — the non-negotiable bit. One process owns all the in-memory
  state and threads.
- `--threads 8` — concurrency for a handful of teachers; requests are short
  (git work is offloaded to the background pusher), so a small thread pool is
  ample.
- `--worker-class gthread` — threaded worker (the default `sync` worker would
  serialize all requests onto one thread).
- **no `--preload`** — so `app:app` is imported *in the worker*, keeping
  `collab.startup()`'s threads/timers in the process that serves.
- `--timeout 120` — generous; nothing in a request path should be slow, but
  avoids killing a worker during an occasional slow git/SQLite moment.

### Why not the alternatives

- **waitress** (pure-Python, single-process, no fork) is also a fine fit and
  even simpler — no fork story to worry about at all. Acceptable substitute if
  we want to avoid gunicorn; the plan below would just swap the dependency and
  the command. Gunicorn is chosen as the more standard answer and for its richer
  ops knobs (graceful timeouts, signals, access logs).
- **uvicorn / hypercorn / ASGI** — overkill; the app is plain WSGI Flask with no
  async.
- **More than one gunicorn worker** — would silently corrupt the model (N push
  queues, N clones racing the volume, N timers). Forbidden.

## Do we need nginx in front of gunicorn?

**No.** The classic "always put nginx in front of gunicorn to buffer slow
clients" advice assumes gunicorn is *directly* internet-facing. On fly it isn't:

- **Fly already fronts us with a proxy.** `[http_service]` + `force_https` means
  every request enters through fly's edge proxy, which terminates TLS and sits
  between the public internet and the container. That *is* the buffering reverse
  proxy gunicorn's docs tell you to deploy behind — adding nginx would stack a
  second proxy behind the first.
- **Slow clients are mainly a `sync`-worker problem, and we use `gthread`.** A
  slowloris-style stall ties up a *thread*, not the whole process; the other
  threads keep serving. Raise `--threads` for more headroom (cheap) rather than
  adding a process.
- **Threat model.** Internal tool, a handful of allowlisted OAuth'd teachers —
  not a public high-traffic slowloris target. An in-container nginx means another
  process to supervise in a single-process-by-design app, plus more Dockerfile
  and config surface, for no real gain.

**Caveat:** fly's proxy *streams* request bodies rather than fully buffering
them, so a very slow upload can still occupy a gthread for its duration. Here the
write paths are tiny markdown/TSV form posts (the heavy git work is offloaded to
the background pusher) and `--timeout 120` caps any single stuck request, so this
is fine. Revisit only if the app's shape changes — public signups, large
uploads, or dropping back to `sync` workers. Even then the fly-native fix is more
threads / fly's own proxy features, not in-container nginx.

## Changes

1. **`pyproject.toml`** — add `gunicorn` to `dependencies` so it lands in the
   frozen venv that the Docker image builds (`uv sync --frozen`). Run
   `uv lock` to update `uv.lock`.

2. **`Dockerfile`** — replace the dev-server CMD:

   ```dockerfile
   CMD ["uv", "run", "gunicorn", "--workers", "1", "--threads", "8", \
        "--worker-class", "gthread", "--bind", "0.0.0.0:8080", \
        "--timeout", "120", "app:app"]
   ```

   Keep the existing `ENV` block. `HOST`/`PORT` are no longer read by gunicorn
   (the bind is explicit), but leaving them is harmless; optionally drop `HOST`
   and keep `PORT` only if we want one source of truth — see step 4.

3. **`app.py`** — no change required to the startup code (it already runs at
   import, which is what we want under one worker). The `if __name__ ==
   "__main__"` block stays as the **local dev** path (`uv run app.py` with the
   Werkzeug reloader). Add a short comment there noting that production is served
   by gunicorn via `app:app`, so this block is dev-only.

4. **Bind port wiring (optional polish).** To avoid hardcoding `8080` in two
   places, either:
   - keep it literal in the Dockerfile CMD (simplest, matches `fly.toml`'s
     `internal_port = 8080`), or
   - use a shell-form CMD that expands `${PORT}`:
     `CMD uv run gunicorn --workers 1 --threads 8 -k gthread --bind 0.0.0.0:${PORT} --timeout 120 app:app`.

   Prefer the literal exec-form (first option) — one fewer shell layer, and the
   port is already pinned by fly.

5. **`DEPLOY.md`** — document the server choice and, prominently, the
   **one-worker rule** and **why** (the in-memory push queue/timers/caches).
   Note that scaling concurrency means raising `--threads`, never `--workers`.

6. **`serve.sh` / local dev** — leave on the Werkzeug dev server (reloader is
   useful locally; the warning is irrelevant off-prod). No change. Optionally add
   a note that prod uses gunicorn.

## Verification

- **Local smoke test of the prod command** (single-user mode is enough to prove
  the server swap):

  ```bash
  uv run gunicorn --workers 1 --threads 8 -k gthread \
      --bind 127.0.0.1:5001 app:app
  ```

  Confirm: no Werkzeug dev-server warning; the home page and a course outline
  render; an edit saves.

- **Collab mode smoke test** with a local `collab.json` (`dev_login: true`,
  `FLASK_SECRET_KEY=dev`, `LESSON_COLLAB_CONFIG`): sign in as an allowlisted
  handle, make an edit, and confirm the **background push** fires and the
  **refresh timer** ticks — i.e. the import-time threads are alive in the worker
  (this is the check that catches a stray `--preload` / fork regression).

- **Deploy** to fly and confirm the boot log shows gunicorn (e.g.
  `Booting worker with pid …`) and **no** development-server warning; do one
  real edit + push round-trip against the live machine.

## Risks / notes

- **`--preload` regression** is the main footgun — it would break the pusher
  silently (requests still serve, pushes never drain). The collab smoke test
  above is the guard; consider a one-line comment in the Dockerfile CMD warning
  against adding `--preload`.
- **`auto_stop_machines = "suspend"`** preserves memory across suspend/resume, so
  timers/threads survive a suspend cycle. (A `stop`/cold start re-imports the app
  and re-runs `startup()` cleanly — also fine.)
- Gunicorn adds one dependency to the image but no new system packages.
- If we later prefer zero added deps, **waitress** is a drop-in alternative
  (`uv run waitress-serve --threads=8 --listen=0.0.0.0:8080 app:app`).
