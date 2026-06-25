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

Your course content (the corpus: one directory per course, each with `plan.md`,
reference `*.md`, `objectives.tsv`, `coverage.tsv`) moves to a new GitHub repo,
e.g. **`lesson-courses`**. The repo's **top level is the corpus** — course
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
- **fly secrets** — the OAuth client secret and the Flask session key.

### 5a. fly secrets

```bash
fly secrets set \
  GITHUB_CLIENT_SECRET="the-oauth-client-secret-from-step-2" \
  FLASK_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

`FLASK_SECRET_KEY` signs the login cookie — make it long and random, and don't
change it casually (changing it logs everyone out).

### 5b. The deploy key and collab.json on the volume

The volume isn't mounted until a machine is running, so deploy once (step 6)
**without** `collab.json` present and the app will boot in single-user mode — or
simply create the files via an SSH session after the first deploy. Easiest order:
do the first `fly deploy` (step 6), then:

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

Then copy the private deploy key onto the volume (from your laptop):

```bash
fly ssh sftp shell
# at the prompt:
put deploy_key /data/deploy_key
exit

# Lock down its permissions (ssh refuses world-readable keys):
fly ssh console -C "chmod 600 /data/deploy_key"
```

`collab.example.json` in this repo is a template for `/data/collab.json`. The
allowlist maps **GitHub handle → role** (`editor` or `viewer`). Edit this file on
the volume whenever you add/remove people or change roles — then restart the
machine (`fly apps restart YOUR-APP`) to pick up allowlist changes.

> First boot will `git clone` the courses repo over SSH. If the host key prompt
> is a worry, the app uses `StrictHostKeyChecking=accept-new`, so the first
> connection trusts github.com automatically.

---

## 6. Deploy

The build needs **both** this repo and its sibling `bells` checkout (the
calendar library is a local path dependency, `../bells/libs/python`). So build
with the **parent directory** as the context:

```bash
# From the directory that contains BOTH `lesson-planning/` and `bells/`:
fly deploy -c lesson-planning/fly.toml \
           --dockerfile lesson-planning/Dockerfile \
           .
```

(The `.` is the build context = the parent dir. The Dockerfile copies
`bells/` and `lesson-planning/` so the path dependency resolves.)

After the first deploy, do step 5b (write `/data/collab.json` + the deploy key),
then restart:

```bash
fly apps restart YOUR-APP
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
every `main_refresh_seconds` (default 5 min) and right after a merge.

---

## Operating notes & troubleshooting

- **"N commits not yet pushed to GitHub."** The commit is safe on the volume; the
  push failed (network, or someone rebased the branch on GitHub). It retries
  automatically and on the next save. If it's stuck, check the deploy key and
  `fly logs`.

- **Sync says "Merge conflict with main."** Two people changed the same lines.
  Resolve it on GitHub (the teacher's branch vs. `main`), then Sync again.

- **A bad file in `main` won't load.** If a malformed course markdown reaches
  `main`, db rebuilds report "corpus didn't load" rather than crashing — the live
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
