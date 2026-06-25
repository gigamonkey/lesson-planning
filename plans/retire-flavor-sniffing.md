# Retire flavor sniffing

Now that a reference hierarchy declares its level names in a required `levels:`
front-matter key (see `FORMAT.md`, format 1.3.0), the detected **flavor**
(`csa`/`csp`/`ib`/`book`/`course`) no longer names levels. It still survives in a
handful of places. This plan inventories what's left and lays out how to remove
flavor as a *concept* to the greatest degree possible — ending with a parser that
reads structure and metadata out of the markdown rather than guessing a flavor
and branching on it.

The thesis: **the markdown format is ours.** A file we load should carry the
metadata we need (levels, kind, and an unambiguous way to read each node's id),
so we never have to infer intent from heading chrome. Getting there cleanly wants
a **major version bump** of the on-disk format (2.0.0) so we can require that
metadata instead of falling back to flavor.

## What flavor sniffing is still used for (the inventory)

In descending order of how load-bearing each one is:

### 1. Extracting the level-1 node id (the only genuinely structural use)

`parse_top_heading` (`hierarchy.py`) reads the root id differently per flavor,
because the level-1 heading formats differ:

- csp `# Big Idea N: TITLE (CODE)` → id is the parenthesized **CODE**
- csa `# Unit N: TITLE` → id is **N**
- ib `# Theme X: TITLE` → id is **X**
- book `# Chapter N: TITLE` → id is **N**

`detect_flavor` calls this on the level-1 heading to label the whole file, and
`parse_sections` calls it again to pull the id. Note that **levels 2+ are already
flavor-independent**: `parse_sections` takes the id as the first whitespace token
(`## 1.1 …` → `1.1`). So flavor only earns its keep on the *root* heading.

### 2. The `course`-flavor bullet form

`detect_flavor` distinguishes `csa` from `course` by heading depth, and
`parse_sections` uses `flavor == "course"` to treat column-0 `- bullets` as
level-3 objectives instead of `###` headings. In practice this almost never fires
through `to_nodes`: the outline (`plan.md`) is parsed by `plan_io.parse_plan`, not
by `hierarchy.to_nodes`. It only matters if a shallow `# Unit` file (depth < 3) is
loaded as a *reference*.

### 3. Default metadata when nothing is specified

`FLAVOR_KIND` (`hierarchy.py`, the default `kind:`) and `FLAVOR_META` / `meta_for`
(`load_nodes.py`, defaults for course / kind / slug / course_title). These are
convenience fallbacks for the CLI (`load_nodes.py`) and the web upload
(`app.py`); every one is overridable by front matter or a flag. No parsing
depends on them.

### 4. Reverse serialization (a fallback)

`to_markdown` → `_flavor_for_tags` + `_level1_heading` (`hierarchy.py`) picks a
heading flavor *from the tag set* to regenerate a reference `.md` from the DB, for
corpus self-containment. It runs only when a reference file is missing on disk
(`plan_io._reference_files`; `write_course` never rewrites an existing reference).
It already bails (`ValueError`) on any vocabulary that doesn't match a known
flavor — e.g. a `unit/lab/page` file can't be round-tripped this way at all.

Flavor is **never persisted** — the `hierarchies` table stores `kind`, the `nodes`
table stores the level `tag`. So removing flavor only touches in-process code; no
schema migration is involved.

## End state (the goal)

- `hierarchy.py` exposes no `flavor` value. `to_nodes`'s output drops the
  `"flavor"` key.
- `LEVEL_TAGS`, `FLAVOR_KIND`, `FLAVOR_META`, `_flavor_for_tags`, `detect_flavor`
  are deleted.
- The reference parser only ever parses **heading-based** references; the
  bulleted course-outline form lives solely behind `plan_io.parse_plan`.
- The level-1 id is read by trying a small list of **id-extraction patterns**
  (plus a generic fallback), with no flavor label propagated anywhere.

## How to remove each use

Taken roughly easiest/most-obviously-removable first, since #3 and #4 are pure
cleanup once the format requires its own metadata.

### Kill #4 — stop regenerating reference markdown from the DB

A reference is **load-only**; its canonical form is the markdown file it was
loaded from, which carries all its metadata. The DB is a cache. So we don't need
to reconstruct reference markdown from the DB at all:

- Treat the on-disk reference `.md` as the sole source of truth. `write_course`
  already skips an existing reference file; make `_reference_files` a no-op (or
  delete it) rather than synthesizing one from nodes.
- Delete `to_markdown`, `_flavor_for_tags`, `_level1_heading` along with it.

**Caveat — the bundle path.** `course_bundle.py` import can create a course whose
reference hierarchies have **no on-disk `.md`**. If that course is later written
to a corpus directory, today `to_markdown` is what would materialize the file.
Options:

- (a) Have the bundle carry each reference's original markdown text verbatim and
  drop it on disk on import (lossless, no regeneration needed). **Preferred.**
- (b) Accept that a bundle-imported reference lives in the DB only and is not
  re-serialized to markdown (write_course skips it; it reloads from the bundle,
  not the corpus).

Either way the DB→markdown reconstruction goes away, and with it #4's dependence
on flavor.

### Kill #3 — require/derive metadata instead of defaulting from flavor

The flavor-derived defaults exist only because old files didn't carry their own
metadata. Once the format requires it (2.0.0), they're redundant:

- `kind:` — make it required in front matter for references (or default to a
  single generic value like `"reference"`), rather than `FLAVOR_KIND[flavor]`.
- `slug` — already defaults to the filename stem (`plan_io._slug_of`); keep that.
- `course` — already supplied by context: `app.py`'s upload fixes the course, and
  `load_nodes.py` takes `--course`. Make it required where it isn't derivable
  instead of falling back to `FLAVOR_META`.
- `course_title` — derive from the course id / `title:` front matter, not flavor.

Then delete `FLAVOR_META` and `meta_for`'s flavor argument (it becomes a thin
"apply overrides over generic defaults" helper, or goes away entirely).

### Kill #2 — the course outline is ours, not a sniffed reference

Treat the bulleted course-outline form as exclusively this software's artifact,
parsed only by `plan_io.parse_plan`:

- Remove the `course` branch from `detect_flavor` / `parse_sections` and the
  bullet-objective synthesis from `hierarchy.py`'s reference path. `to_nodes`
  then only parses heading-based references.
- If we ever want to ingest an *external* outline-shaped file, do it through an
  explicit, separate entry point that routes to `plan_io.parse_plan` — a
  deliberate "load this as an outline" action, not depth-based auto-detection
  inside the reference parser.

This also removes the only reason `detect_flavor` has to look at the whole
document's depth rather than just the first heading.

### Kill #1 — regexes as id-parsers, not flavor labels

This is the irreducible bit: the root heading still embeds its id in a
format-specific spot. But that doesn't require the *concept* of a flavor — it
requires a way to pull an id (and title) out of a top heading. Replace
`parse_top_heading` with an id-extractor:

```python
# Ordered (pattern, id-group, title-group) attempts. First match wins.
ROOT_ID_PATTERNS = [
    (BIG_IDEA, "code", "title"),   # # Big Idea N: TITLE (CODE) -> id = CODE
    (UNIT,     "num",  "title"),   # # Unit N: TITLE            -> id = N
    (THEME,    "id",   "title"),   # # Theme X: TITLE           -> id = X
    (CHAPTER,  "num",  "title"),   # # Chapter N: TITLE         -> id = N
    (GENERIC,  "id",   "title"),   # # ID TITLE  (first token)  -> new formats
]

def parse_root_heading(rest):
    """(id, title) from a level-1 heading. No flavor."""
    for pat, idg, titleg in ROOT_ID_PATTERNS:
        m = pat.match(rest)
        if m:
            return m.group(idg), m.group(titleg)
    sys.exit(f"unparseable top-level heading: {rest!r}")
```

Key differences from today:

- Returns **only** `(id, title)` — no flavor. Nothing downstream branches on the
  result beyond "this is the id."
- Adds a **generic fallback** (`# ID TITLE`, id = first token, like every deeper
  level) so a brand-new format works without adding a regex — the same uniformity
  the deeper levels already enjoy.
- The named patterns become pure backward-compat sugar for the existing
  extractor output. We could even, at 2.0.0, ask producers to emit the generic
  `# ID TITLE` form and drop the named patterns entirely — at which point #1
  vanishes too and the level-1 heading is parsed exactly like levels 2+.

**Stretch goal — fully uniform headings.** If the extractors emit `# ID TITLE` at
level 1 (e.g. `# CRD Creative Development` rather than
`# Big Idea 1: Creative Development (CRD)`), then id extraction is identical at
every depth, `to_nodes` needs no special root handling, and there is no place left
that knows about flavors. This is the cleanest possible end state; its cost is a
real format change for the producer side (the extractors), which is acceptable
under a 2.0.0 bump.

## The enabling change: format 2.0.0

These removals lean on being able to *require* metadata rather than infer it.
That's a breaking change to the on-disk contract, so:

- Bump `FORMAT_VERSION` to `2.0.0` and `load_nodes.FORMAT_MAJOR` to `2`.
- 2.0.0 requires `levels:` (already true) and `kind:` on references, and treats
  the level-1 heading as "id-bearing like any other heading" (named patterns kept
  as compatibility sugar, or dropped in favor of the generic form — decide below).
- Regenerate the committed corpus and fix the extractors (`hierarchy-extractors`)
  to emit conforming 2.0.0 markdown.

## Suggested sequencing

1. **#4 first — DONE.** Deleted `to_markdown`, `_flavor_for_tags`,
   `_level1_heading`. References now stash their **verbatim source markdown** in a
   new `hierarchies.source_md` column (populated on every load: corpus read, app
   upload, CLI), and `write_course` / `_reference_files` replay that text exactly
   rather than reconstructing markdown from db nodes. The bundle carries
   `source_md`, so a bundle-imported course (no on-disk `.md`) still writes its
   reference files on `write_course` — option (a), realized via the db rather than
   a separate bundle field. `ensure_schema` backfills the column on existing dbs.
   Replay is byte-for-byte (better fidelity than `to_markdown` had), and the
   bulleted/`unit/lab/page` cases `to_markdown` couldn't represent now round-trip
   for free. `LEVEL_TAGS` is now dead code, retained until step 4.
2. **#3** — require `kind:` (and `course` where needed); delete `FLAVOR_META` /
   `FLAVOR_KIND`. Bump to 2.0.0 here.
3. **#2** — drop the `course` branch from the reference parser; document that the
   outline is loaded only via `plan_io.parse_plan`.
4. **#1** — turn `parse_top_heading` into `parse_root_heading` (id only) with a
   generic fallback; delete `detect_flavor`, `LEVEL_TAGS`, and the `"flavor"` key
   from `to_nodes`'s output. Update `load_nodes`/`app.py` callers that read
   `doc["flavor"]`.

After step 4, grep for `flavor` should return only historical mentions in
comments/plans.

## Open questions

- **Named patterns vs. fully generic root headings.** Keep `# Unit N:` /
  `# Big Idea N: … (CODE)` as parseable compatibility sugar indefinitely, or
  require the generic `# ID TITLE` form at 2.0.0? The former is gentler on the
  extractors; the latter actually deletes #1. Leaning toward keeping the named
  patterns as a thin id-extractor (no flavor) and adding the generic fallback, so
  both old and new files load and *nothing* is labeled a flavor.
- **CSP's display number.** `# Big Idea 1: … (CRD)` carries both a sequence number
  (1) and the real id (CRD). The generic form would keep only one. If the "Big
  Idea N" display text matters, it has to live in the node title, not be
  reconstructed — which is fine, since references are load-only.
- **Bundle round-trip.** Confirm `course_bundle.py` can carry (or re-derive)
  reference markdown once `to_markdown` is gone; pick option (a) above.
