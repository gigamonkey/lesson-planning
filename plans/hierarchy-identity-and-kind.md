# Hierarchy identity, title, and kind

`kind` is doing too much. It is, today, simultaneously: a human-readable
provenance label, an *identity* ingredient (the app derives a reference's slug as
`{course}-{kind}`), a fallback source for the display *title*, the
outline-vs-reference signal (`kind='course-outline'`), and a closed set that
drives per-kind pill styling. This conflation produces a concrete bug — **you
can't attach two hierarchies of the same kind to one course** (two textbooks
collide on the slug `{course}-book` and the second silently overwrites the
first's `.md`).

This plan untangles the three handles a hierarchy actually has — **slug**
(identity), **title** (label), **kind** (provenance) — so each is independent and
none is derived from another, and fixes the same-kind bug as a consequence.

This is a sibling to [retire-flavor-sniffing.md](retire-flavor-sniffing.md): that
plan moved *structure/level* metadata into the markdown; this one does the same
for a hierarchy's *identity* metadata.

## Facts (established)

About hierarchies and a course:

1. A course has **many** hierarchies. One is the distinguished **outline** (this
   app's primary job, the authored lesson plan); the rest are references.
2. References are usually **uploaded markdown**, saved into the course directory
   (the corpus is the source of truth) *and* parsed/cached in the db.
3. A hierarchy may be **editable** beyond the outline (e.g. outlining a new
   textbook) — a secondary function — so `editable` does **not** identify the
   outline. The authoritative outline pointer is `courses.primary_outline`
   (`kind='course-outline'` and `editable=1` are redundant secondary signals).

About the slug (identity):

4. The slug is the hierarchy's identity: the **globally** unique `hierarchies`
   primary key, the foreign key in `coverage.tsv` / `hierarchy_targets` /
   `node_attr` / `node_duration`, **and** a URL segment (`/<course>/h/<slug>`).
   Renaming a slug breaks coverage edges and links.
5. Global uniqueness is currently held up by hand convention (filenames are
   course-prefixed: `csa-ced.md`) plus a collision check in bundle import — there
   is no generative scheme. The **outline already** solves this by course-scoping
   its slug (`csa-plan`) and pinning it in the `primary_outline:` front-matter key
   (every plan file is named `plan.md`, so the filename alone would collide).
6. The documented `slug:` front-matter override is **dead for references**:
   `hierarchy.to_nodes` doesn't emit `slug`, so `read_course`'s
   `doc.get("slug")` is always `None` and every reference falls back to its
   filename stem. The outline path honors `meta.get("slug")` via `_slug_of`;
   references don't. (FORMAT.md claims the override works — it doesn't, yet.)

About title and kind:

7. `title:` is currently **optional**; when absent the displayed title is derived
   as `course.upper()` + a tidied `kind` (`hierarchy_title` → "CSA CED"). So the
   title is entangled with kind too.
8. `kind` for a reference is **pure display** — nothing branches on it. It is
   shown in the sidebar as a styled pill (`pill-{{ kind }}`). Only the *outline's*
   `kind='course-outline'` is load-bearing, and that's redundant with
   `primary_outline`.
9. `kind` tells you nothing about **structure** — CSA and CSP CEDs share kind
   `ced` but use different level vocabularies (that's what `levels:` carries).
10. Reframed as **provenance** (College Board CED, IB syllabus, a textbook, an
    online curriculum like BJC), there is no reason a course can't have **two
    hierarchies of the same kind** (e.g. two textbooks). Today's values
    (`ced`/`syllabus`/`book`) also conflate *issuer* and *document-type*.

## Decisions

### Slug — course-relative (bare) on disk, course-scoped in the db

A hierarchy's identity is **`(course, bare-slug)`**. The course namespace is
implicit from the directory the file lives in, so everything *on disk* uses the
**bare** (course-relative) slug; the course scope is supplied at load time. This
makes a reference file **portable** — drop the same `ced.md` (`slug: ced`) into
two course directories and it loads correctly in each (two independent copies,
not one shared file; edits don't propagate).

- **Default the bare slug from the uploaded filename stem**, then let the user
  **edit it at upload time** (uploaded names carry cruft —
  `csp-ced-2023-my-cleaned-up-version.md` → user trims to `ced-2023`). If the user
  leaves the course prefix on, strip it back off for the on-disk bare form.
- **Validate**: `[a-z0-9-]+`, non-empty (it's a filename stem and a URL segment).
- **Pin** the bare slug in the saved file's front matter (`slug:`) **and** name
  the file `{bare-slug}.md`. The front-matter slug is authoritative on load
  (robust to later file renames — coverage stays valid); the filename is
  convention. Wiring fix required: `read_course` must read the reference slug from
  `meta` (like `_slug_of`), not from `doc` (see fact 6).
- **Everything else on disk is bare too** — not just the front matter.
  `coverage.tsv`, the outline's `targets:`, and `primary_outline:` all currently
  store the *global* slug (`csa-book`, `csa-ced`, `csa-plan`); for portability
  they must store the bare slug (`book`, `ced`, `plan`), with the loader scoping
  them to the course and `write_course` stripping back. Bonus: this dissolves the
  per-course `primary_outline: csa-plan` dance — it becomes `primary_outline:
  plan` everywhere (the explicit prefix existed only to dodge the "every plan.md
  is named `plan`" collision, which load-time scoping removes).
- **Uniqueness is per-course on the bare slug** — exactly the natural condition,
  *provided the db keys on `(course, slug)` and does not flatten to a string*.
  See the composite-key decision below; flattening reintroduces a cross-course
  collision and forces a global check.

This generalizes what the outline already does (a course-scoped, front-matter
pinned slug) to all hierarchies, and pushes it one step further by making the
stored form course-relative.

### Db key — composite `(course, slug)`, not a flattened global string

The prefixing scheme has two implementations; this is the real fork:

- **Composite `(course, slug)` key (preferred).** The hierarchy's identity *is*
  the pair, so model it directly. Uniqueness = "bare slug unique within course"
  (sufficient and natural); no globalize/localize translation; URLs become
  `/<course>/h/<bare-slug>` (`/csa/h/ced`), with the existing stale-slug redirect
  covering old `/csa/h/csa-ced` links. Cost: every FK on the slug grows a
  `course` column — `nodes`, `coverage`, `node_attr`, `node_duration`,
  `hierarchy_targets`, and `courses.primary_outline` — a real schema change.
- **Flattened global string `"{course}-{slug}"` (less churn, leakier).** Keep the
  single-column slug PK; scope on load by prepending `{course}-`, localize on
  write/display/URL. Cost: a translation layer across ~6 boundaries (miss one and
  a slug is sometimes prefixed, sometimes not), *and* a residual collision —
  `csp` + `honors-x` and `csp-honors` + `x` both flatten to `csp-honors-x`, so it
  still needs a global uniqueness check (or a separator reserved out of course
  ids and slugs).

Leaning composite: the bare-slug model is essentially an argument for treating
`(course, slug)` as the key, since flattening is what created the prefix
gymnastics (and the `primary_outline` workaround) in the first place.

### Warn on filename/slug mismatch

Once the slug is pinned, the filename is cosmetic but should still match. On load
(`read_course`, so `seed`/`rebuild_db` and the app's restore surface it):

- stem ≠ this file's own pinned bare slug → **warn**. The safe fix is to **rename
  the file** to `{bare-slug}.md`, *never* to edit the `slug:` to match the
  filename — editing the slug changes identity and orphans coverage. The message
  should say so.
- This is not cosmetic: `write_course` writes references as `{bare-slug}.md`, so a
  mismatched filename **spawns a second file** on the next save, and then two
  files claim one slug → a real collision on the next load.
- two files in one course resolving to the **same** bare slug → **error** (the
  course can't have two hierarchies with one identity; load can't represent both).

### Title — the real, required label

- Make `title:` **required** in reference front matter and stop deriving it from
  kind (drop the `hierarchy_title(course, kind)` fallback). The title is the human
  label shown in the sidebar (fact 7); the producer knows it.
- (Open: whether to keep `hierarchy_title` for any outline/default case, or delete
  it once nothing falls back to it.)

### Kind — optional, free-form provenance

- Redefine `kind` as **provenance**: where the hierarchy came from (College Board
  CED, IB syllabus, a specific textbook, BJC, …). Free-form text, **optional**
  (it's a label, and nothing branches on it).
- It may **repeat within a course** (two textbooks) — it is no longer part of the
  slug, so there's nothing to collide.
- Reverse the step-2 decision that made `kind:` *required*. Step 2 of
  retire-flavor-sniffing required it to kill the `FLAVOR_KIND` default; that was
  the right call *given kind was still load-bearing for identity/title*. Once slug
  and title no longer lean on kind, kind can relax back to optional.
- Sidebar pill: a free-form string needs a **default pill style** (today's closed
  set gets bespoke per-kind colors); render the pill only when kind is present.

### Outline identity — `primary_outline` only

- `courses.primary_outline` is the **sole** authority for "which hierarchy is the
  outline." Drop reliance on `kind='course-outline'` (and on `editable`, since
  other hierarchies may be editable — fact 3). The `kind='course-outline'`
  tiebreaks in `outline_hierarchy` / the `primary_outline` backfill go away.
- Consequence: the outline doesn't need a "kind" at all (it's not a provenance).

### Create vs. update — two distinct actions

The current upload is an upsert (re-uploading a slug replaces that hierarchy, with
an orphaned-coverage warning). "Reject a new upload whose slug already exists"
conflicts with the legitimate "re-upload a cleaned-up version of *this* reference"
flow. Split them:

- **Add a new reference** — bare slug must be unused in this course (per-course
  check; the filename-defaulted, editable-slug upload above).
- **Replace this reference's source** — same slug, intentional, triggered from
  that hierarchy's own controls; keeps the existing orphaned-coverage warning.

## Consequences / cleanup

- `hierarchies.source` (the original filename) becomes vestigial once slug is the
  identity and source markdown is stored in `source_md` — drop it or repurpose.
- FORMAT.md: document `slug:` as the real (now-wired) pinned identity, `title:` as
  required, `kind:` as optional free-form provenance; note the filename ↔ slug
  convention and the rename-the-file guidance.
- Another `FORMAT_VERSION` bump (title required, kind optional) — fold into the
  2.0.0 work if it lands together, else 2.1.0/3.0.0 as the breakage dictates.

## Suggested sequencing

1. **Wire the reference slug from `meta`** (fact 6 fix) so `slug:` actually pins —
   pure bug fix, makes FORMAT.md honest, unblocks everything else.
2. **Pick the db key** (composite `(course, slug)` vs flattened string) — this
   gates the rest; composite is the bigger schema change but the cleaner model.
3. **Move on-disk slugs to bare/course-relative** — `coverage.tsv`, `targets:`,
   `primary_outline:`, and reference front matter; loader scopes to the course,
   `write_course` writes bare.
4. **Upload UX**: filename-default bare slug, validate, per-course uniqueness
   check, write `slug:` into the saved front matter + name the file
   `{bare-slug}.md`. Fixes the two-textbooks bug.
5. **Mismatch/collision warnings** in `read_course`.
6. **Title required**, drop the kind-derived title fallback.
7. **Kind → optional free-form provenance**; relax the step-2 requirement; default
   pill style.
8. **`primary_outline` as sole outline identity**; remove the
   `kind='course-outline'` tiebreaks.

## Open questions

- **Composite key vs. flattened string** (see the db-key decision) — really a
  question of appetite for the schema change: a `course` column on `nodes`,
  `coverage`, `node_attr`, `node_duration`, `hierarchy_targets`, and
  `primary_outline`, plus the in-place migration. Composite is the cleaner model;
  flattened is the smaller diff with a translation layer and an edge-case check.
- **URL form** — `/<course>/h/<bare-slug>` (clean, needs the stale-slug redirect
  for old `/<course>/h/<global-slug>` links) vs. keeping the global form in URLs.
- **Where does the editable-slug field live in the upload UI?** A confirm step
  after choosing the file (prefilled slug/title/kind), vs. an inline form. The
  override plumbing already exists (`over("hierarchy")` etc.); only the surfaced,
  prefilled, validated field is new.
- **Do we *require* `slug:` in front matter, or keep stem-fallback?** Leaning:
  optional with stem-fallback (backward compatible; a file with no `slug:` is
  pinned-to-stem), and the upload always writes one. Hand-authored corpus files
  without `slug:` keep working.
- **Provenance vocabulary:** purely free-form, or a soft known-set with an "other"
  escape (for nicer pills / filtering)? Free-form is simplest; a known-set buys
  consistent styling.
- **`hierarchy_title` / `kind_label`:** how much survives once title is required
  and kind is free-form provenance.
