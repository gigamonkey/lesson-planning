# Deploying lesson-planning for git-backed collaboration

This guide stands the app up on **fly.io** so other teachers can sign in with
GitHub, view courses, and (if they're editors) edit them — with every change
committed to a per-teacher branch in a separate **courses repo** and merged via
normal GitHub pull requests. The design is `plans/git-collaboration.md`; this
file is the operational checklist.

You'll do six things:

1. Split the courses into their own repo.
2. Register a GitHub OAuth app (for sign-in).
3. Mint an SSH deploy key (for the app to push).
4. Create the fly app + volume.
5. Put the config + secrets in place.
6. Deploy, then add teachers.

Throughout, **editors** get a private sandbox (their own db + git worktree +
branch); **viewers** see a shared read-only view of `main`. You can deploy with
just yourself as editor and everyone else as viewer, then promote people as you
go — that's the recommended way to shake out the kinks.

> **Single machine, always.** The per-user SQLite caches, the git clone, and the
> in-process push queue / commit-message buffers all live on one machine's volume
> and memory. Never scale this to more than one machine.

---

## 0. How "collaboration mode" turns on

The app is the same single-user tool it has always been **unless a
`collab.json` is present** (path: `LESSON_COLLAB_CONFIG`, default
`/data/collab.json` in the container). When that file exists and names a `repo`,
the app:

- requires GitHub sign-in,
- binds each request to the logged-in user's sandbox,
- commits + pushes on save,
- merges `main` back into worktrees on sync.

No `collab.json` ⇒ none of that runs. So local `uv run app.py` is unaffected.

---

## 1. Split the courses into their own repo

Your course content (the courses directory: one directory per course, each with `plan.md`,
reference `*.md`, `objectives.tsv`, `coverage.tsv`) moves to a new GitHub repo,
e.g. **`lesson-courses`**. The repo's **top level is the courses directory** — course
directories sit directly at the root.

```bash
# From a fresh checkout location:
mkdir lesson-courses && cd lesson-courses
git init -b main

# Copy your course directories to the repo root (NOT under a courses/ subdir).
# For example, to start from the bundled example:
cp -r /path/to/lesson-planning/examples/widgets ./widgets

git add -A
git commit -m "Initial courses"
# Create the repo on GitHub (private is fine), then:
git remote add origin git@github.com:YOUR-USER/lesson-courses.git
git push -u origin main
```

Two repo settings to set on GitHub now:

- **Branch protection on `main`** (Settings → Branches): require a PR to merge,
  so course edits land through review.
- **Merge button: allow "Create a merge commit"; consider disabling "Squash".**
  The app merges `main` back into each editor's branch; with merge-commit PRs
  that back-merge is conflict-free. Squash-merging makes `main` non-ancestral to
  the branch's commits and can cause spurious conflicts on the next sync (see
  `plans/git-collaboration.md` → "PR merge-strategy caveat").

---

## 2. Register a GitHub OAuth app (sign-in)

This authenticates teachers to the app. It does **not** grant push rights — that's
the deploy key in step 3.

1. GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**
   (or under your org's settings if you prefer org-owned).
2. Fill in:
   - **Application name:** Lesson Planning
   - **Homepage URL:** `https://YOUR-APP.fly.dev`
   - **Authorization callback URL:** `https://YOUR-APP.fly.dev/oauth/callback`
     (exactly this path — the app serves the callback there).
3. Create it, then **generate a client secret**. Note the **Client ID** and
   **Client secret** — they go into config/secrets below.

(If you don't know the fly hostname yet, do step 4 first to create the app, then
come back and fill in the real `YOUR-APP.fly.dev`.)

---

## 3. Mint an SSH deploy key (push access)

The app pushes every teacher's branch to `lesson-courses` using a single SSH
**deploy key** scoped to that one repo. Generate a dedicated keypair (no
passphrase, since the app uses it unattended):

```bash
ssh-keygen -t ed25519 -N "" -C "lesson-planning-deploy" -f ./deploy_key
# produces ./deploy_key (private) and ./deploy_key.pub (public)
```

Install the **public** half on the courses repo:

- GitHub → `lesson-courses` → **Settings → Deploy keys → Add deploy key**
- Title: `lesson-planning app`
- Key: paste the contents of `deploy_key.pub`
- **Check "Allow write access"** (the app must push).

Keep the **private** half (`deploy_key`) — it goes onto the fly volume in step 5.
Both `deploy_key` and `deploy_key.pub` are gitignored; never commit them.

> If the key ever leaks, the blast radius is write access to this one content
> repo. Rotate by deleting the deploy key on GitHub and repeating this step.

---

## 4. Create the fly app + volume

Install [`flyctl`](https://fly.io/docs/flyctl/install/) and sign in
(`fly auth login`). Then, from the **lesson-planning** repo:

```bash
# Pick a unique app name; this becomes YOUR-APP.fly.dev.
fly apps create YOUR-APP

# Edit fly.toml: set `app = "YOUR-APP"` and `primary_region` to your area
# (e.g. "sea", "ord", "iad"). List regions with `fly platform regions`.

# Create the persistent volume the app stores everything on (one machine!).
fly volumes create lesson_data --region YOUR-REGION --size 1   # 1 GB is plenty
```

---

## 5. Put the config + secrets in place

There are two kinds of configuration:

- **`collab.json`** — non-secret settings (repo URL, allowlist, OAuth client id).
  It lives on the **volume** at `/data/collab.json` (so it's easy to edit without
  redeploying), and it holds the **allowlist**.
- **fly secrets** — the OAuth client secret, the Flask session key, and the SSH
  deploy key. These come from a local **`.env`** (+ the `deploy_key` file) and are
  pushed by `set-secrets.sh`, which `make deploy` runs for you — no `fly secrets
  set` by hand.

### 5a. fly secrets (from `.env`)

`template.env` documents every secret the app depends on. Copy it to a gitignored
`.env` and fill in real values:

```bash
cp template.env .env
# Edit .env: set GITHUB_CLIENT_SECRET (from step 2) and FLASK_SECRET_KEY, e.g.
#   python3 -c 'import secrets; print(secrets.token_hex(32))'
```

`FLASK_SECRET_KEY` signs the login cookie — make it long and random, and don't
change it casually (changing it logs everyone out). You can also move the
non-secret `GITHUB_CLIENT_ID` / `LESSON_COURSES_REPO` into `.env` instead of
`collab.json` if you prefer one place for everything (see the comments in
`template.env`).

`set-secrets.sh` (run by `make deploy`, or on its own to rotate secrets) stages
these on fly: `fly secrets import` for the `.env` values, plus a `fly secrets set`
for the multi-line **deploy key** read from the `deploy_key` file minted in step
3. The app writes that key onto the volume at startup, so there's no manual
`sftp` step.

**Optional — instant main refresh.** Set `LESSON_GITHUB_WEBHOOK_SECRET` in `.env`
(a fresh `python3 -c 'import secrets; print(secrets.token_hex(32))'`) to enable the
`POST /github/webhook` endpoint, then add a webhook in the **courses** repo
(GitHub → Settings → Webhooks → Add webhook): Payload URL
`https://<app>.fly.dev/github/webhook`, content type `application/json`, the same
secret, "Just the push event". A push to `main` then refreshes the viewers' view
in seconds instead of waiting on the poll; GitHub's **Recent Deliveries** tab
shows a `202`. Leave the secret unset to stay poll-only (the endpoint 404s).

### 5b. collab.json on the volume

The volume isn't mounted until a machine is running, so deploy once (step 6)
**without** `collab.json` present and the app will boot in single-user mode, then
create the file via an SSH session. Easiest order: do the first `make deploy`
(step 6), then:

```bash
# Open a shell on the running machine (mounts /data).
fly ssh console

# Inside the machine:
mkdir -p /data
cat > /data/collab.json <<'JSON'
{
  "repo": "git@github.com:YOUR-USER/lesson-courses.git",
  "data_dir": "/data",
  "allowlist": {
    "your-github-handle": "editor"
  },
  "github_oauth": { "client_id": "YOUR_OAUTH_CLIENT_ID" },
  "ssh_key_path": "/data/deploy_key",
  "main_refresh_seconds": 300,
  "dev_login": false
}
JSON
exit
```

The deploy key itself doesn't need copying — `set-secrets.sh` sends it as the
`LESSON_DEPLOY_KEY` secret (step 5a) and the app writes it to
`ssh_key_path` (`/data/deploy_key`) with the right permissions on startup.

`collab.example.json` in this repo is a template for `/data/collab.json`. The
allowlist maps **GitHub handle → role** (`editor` or `viewer`). Edit this file on
the volume whenever you add/remove people or change roles — then restart the
machine (`fly apps restart YOUR-APP`) to pick up allowlist changes.

> First boot will `git clone` the courses repo over SSH. If the host key prompt
> is a worry, the app uses `StrictHostKeyChecking=accept-new`, so the first
> connection trusts github.com automatically.

---

## 6. Deploy

The calendar library (`bell-schedule`) and its bundled data (`bhs-calendars`)
are PyPI dependencies now, so the build context is just this repo — no sibling
checkout needed. `make deploy` stages the secrets from `.env` (step 5a) and then
ships the app:

```bash
# From the lesson-planning repo:
make deploy
```

(That's `./set-secrets.sh && fly deploy`. Use plain `fly deploy` to ship without
re-staging secrets, or `make secrets` to stage them without deploying.)

After the first deploy, do step 5b (write `/data/collab.json`), then restart:

```bash
make restart        # fly apps restart <app from fly.toml>
```

Visit `https://YOUR-APP.fly.dev` — you should be redirected to a sign-in page.
Click **Continue with GitHub**, authorize, and you'll land in the app as an
editor.

---

## 7. Day-to-day: adding teachers and the edit/PR loop

**Add a teacher:** edit `/data/collab.json`'s `allowlist` (handle → `editor` or
`viewer`) via `fly ssh console`, then `fly apps restart YOUR-APP`.

**The editor loop** each teacher follows:

1. Sign in → they get a private workspace (branch `their-handle`).
2. Edit and **Save**. Each save commits to their branch (authored as them) and
   pushes to `origin/their-handle`. A sidebar badge shows pending pushes.
3. When ready, they open a **pull request** on GitHub from `their-handle` → `main`
   and it's reviewed/merged like code.
4. Everyone picks up merged changes with the **Sync** button (also runs
   automatically at sign-in), which merges `main` into their branch.

**Viewers** see the shared, read-only `main` view; it refreshes automatically
every `main_refresh_seconds` (default 5 min) and right after a merge — or in
seconds on every push if you wired up the GitHub webhook (step 5a).

---

## Operating notes & troubleshooting

- **"N commits not yet pushed to GitHub."** The commit is safe on the volume; the
  push failed (network, or someone rebased the branch on GitHub). It retries
  automatically and on the next save. If it's stuck, check the deploy key and
  `fly logs`.

- **Sync says "Merge conflict with main."** Two people changed the same lines.
  Resolve it on GitHub (the teacher's branch vs. `main`), then Sync again.

- **A bad file in `main` won't load.** If a malformed course markdown reaches
  `main`, db rebuilds report "courses directory didn't load" rather than crashing — the live
  view keeps its last good state. Fix the file via a PR. (Keep `main` clean by
  reviewing PRs.)

- **Changing the allowlist or OAuth client id** requires a machine restart
  (`fly apps restart`) since `collab.json` is read at startup. Secrets
  (`fly secrets set`) trigger a redeploy automatically.

- **Local testing without GitHub.** Set `"dev_login": true` in a local
  `collab.json` and run `LESSON_COLLAB_CONFIG=./collab.json FLASK_SECRET_KEY=dev
  uv run app.py`. The sign-in page then offers a "Dev sign in" box that logs you
  in as any allowlisted handle with no OAuth. **Never enable `dev_login` in
  production.**

- **Backups.** The durable source of truth is the GitHub courses repo (every save
  is pushed). The volume is a cache + working area; losing it loses only
  unpushed commits and in-flight sandboxes, which rebuild from the repo on next
  login.

- **The app is served by gunicorn, with exactly one worker.** The image's `CMD`
  runs `gunicorn --workers 1 --threads 8 -k gthread … app:app` (a real WSGI
  server, not Flask's dev server). The single-worker rule is **load-bearing, not
  a default**: the app keeps live state in one process's memory — the background
  push queue + worker thread, the refresh/autosave timers, the per-handle SQLite
  caches, and the single git clone on the volume. Scale concurrency by raising
  `--threads`, **never** `--workers` (matching `fly.toml`'s single-machine rule).
  Do **not** add `--preload`: it would run `collab.startup()` in the master and
  fork workers where its threads are dead, so requests would serve but pushes
  would silently never drain. No reverse proxy (nginx) is needed in the container
  — fly's edge proxy already terminates TLS and fronts the app. See
  `plans/production-wsgi-server.md` for the full rationale.
