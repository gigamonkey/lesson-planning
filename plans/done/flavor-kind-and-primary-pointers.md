# Plan: Split flavor/kind in the format; make primary reference + outline explicit

## Goal

Two related model changes, decided with the user:

- **A. Carry both `flavor` and `kind` in the node-list JSON.** `flavor` describes
  the *syntax* of the source markdown (its heading shape / id style: `csa`, `csp`,
  `ib`, `book`); `kind` describes the hierarchy's *purpose* (`ced`, `syllabus`,
  `book`). Today the consumer derives kind from flavor via `FLAVOR_META`; the
  producer actually knows the purpose, so it should stamp it.

- **B. Make "the course's primary reference" and "official outline" explicit
  course-level pointers**, replacing the implicit `kind='ced'` /
  `kind='course-outline'` tiebreaks. Primacy is a per-course choice *among*
  hierarchies — not a property of a hierarchy or its kind — so it belongs on the
  course, and a single hierarchy JSON can't carry it.

These untangle three axes that are currently conflated: **flavor** (syntax),
**kind** (purpose), and **primacy** (the course's chosen one).

## Why now / what it enables

- Two references where you pick which is authoritative (e.g. a CED *and* a book,
  CED primary); the rename of `ib-syllabus`→`syllabus` falls out naturally since
  `flavor` already carries the `ib`.
- In principle multiple outlines with one declared official.
- `kind='ced'` stops doubling as "the important one" — it just means "is a CED."

## Part A — `flavor` + `kind` in the JSON format (cross-repo)

### Producer (hierarchy-extractors repo)

- `build_hierarchy_json.py` emits a top-level **`kind`** alongside `flavor`,
  defaulting from a flavor→kind map (`csa`/`csp`→`ced`, `ib`→`syllabus`,
  `book`→`book`), overridable with `--kind`. (The kind policy moves from the
  consumer to the producer, where the purpose is actually known.)
- `json-format.md`: document `kind`; spell out flavor = syntax/id-shape vs
  kind = purpose; bump the format to **1.1.0** (a backward-compatible addition).
  Note kind's vocabulary (`ced`, `syllabus`, `book`) and that `course-outline` is
  a consumer-only kind never produced here.

### Consumer (this repo)

- Kind resolution order becomes **explicit override → `doc["kind"]` → flavor
  fallback (`FLAVOR_META`)**. Touch the three load paths that compute kind:
  `app.hierarchy_load_course` (`app.py:546`), `seed._load_hierarchy`
  (`seed.py:67`), `rebuild_db.load_reference_nodes` (`rebuild_db.py:50`) — each
  currently `over("kind") or meta_for(flavor)["kind"]`; insert `doc.get("kind")`
  in the middle.
- `load_nodes.load_doc` still just checks the **major** version (accepts `1.x`);
  no change needed there.
- Standardize the `syllabus` name: `FLAVOR_META["ib"]["kind"]` → `"syllabus"`.

### Rename fallout (`ib-syllabus` → `syllabus`)

- CSS pill class `pill-ib-syllabus` → `pill-syllabus` (`base.html`,
  `objectives.html` otag styles).
- `objectives.html:31` header label map `{'ib-syllabus':'ib', ...}` → drop the
  entry (`syllabus` is already short).
- `kind_label` (`load_nodes.py:83`) still yields "syllabus" either way, so display
  is unchanged.
- Migration (see Part B's migration block): `UPDATE hierarchies SET kind='syllabus'
  WHERE kind='ib-syllabus'`.

## Part B — explicit primary pointers (schema + consumer)

### Schema

Add two nullable columns to `courses`:

```sql
ALTER TABLE courses ADD COLUMN primary_reference TEXT REFERENCES hierarchies(hierarchy);
ALTER TABLE courses ADD COLUMN primary_outline   TEXT REFERENCES hierarchies(hierarchy);
```

(FK enforcement is currently off, so also clear these in code on delete — see
below — rather than relying on `ON DELETE SET NULL`.)

### Setting the pointers

- `ensure_outline` sets `primary_outline` to the outline it creates (if unset).
- Loading a reference: if the course has no `primary_reference` yet, set it to this
  one — so the first reference is primary by default (`hierarchy_load_course`,
  `seed._load_hierarchy`).
- Deletes clear a dangling pointer: `hierarchy_delete` and `course_delete` set the
  course's pointer to NULL (or re-pick a remaining reference) when it targets the
  deleted hierarchy.

### Reads that switch from `kind=` tiebreak to the pointer

Functional (must change):

- `reference_hierarchy` (`app.py:250`) → `courses.primary_reference`, falling back
  to any reference if NULL.
- `outline_hierarchy` (`app.py:258`) → `courses.primary_outline`, fallback as today.
- `render_outline.fetch` R/O selection (`render_outline.py:20,23`) → read the
  course pointers (fallback to the old `kind=` order for un-migrated data).
- `import_objectives.reference_slug` (`import_objectives.py:111`) → prefer
  `primary_reference`.
- `inject_nav` outline detection (`app.py:302`) → `h == primary_outline` instead of
  `kind == 'course-outline'`.
- `seed._ensure_outline` (`seed.py:53`) → use/set the pointer.

Display-ordering only (can stay, or sort primary-first for polish):
`inject_nav`/`objectives`/`setup` `ORDER BY (kind='ced') DESC` (`app.py:295,516,828`).

### UI

On the per-course **Setup page**, add a control to choose the **primary
reference** among the course's references (radio or select → `POST
/<course>/primary` sets `courses.primary_reference`). The current reference list
gains a "primary" marker. An **official-outline** selector is only meaningful once
a course can have >1 outline — note it but defer the UI (the pointer + default
cover today's single-outline case).

### Persistence (the `courses`-not-exported gap)

`export_planning` does **not** export `courses`, so `title` and the new pointers
would be lost on export→`rebuild_db` (a pre-existing gap for `title`). Fix by
adding `courses` to the export:

- `export_planning.TABLES`: add `"courses": (["course","title","primary_reference",
  "primary_outline"], ["course"], None)`.
- `import_planning` restores it; order it **after** `hierarchies` so the pointers
  resolve (FK is off, but keep it sane).
- `course_bundle` export/import: include both pointers in the bundle's `course`
  object (`course_bundle.py:export_course`/`import_course`).

## Migration (in `ensure_schema`)

Idempotent, on existing working dbs:

1. `ALTER TABLE courses ADD COLUMN primary_reference/primary_outline` (guard on
   `PRAGMA table_info`).
2. Backfill per course from today's implicit choice: `primary_outline` = the
   `editable=1, kind='course-outline'` hierarchy; `primary_reference` = the
   `editable=0` reference ordered `(kind='ced') DESC, hierarchy`.
3. `UPDATE hierarchies SET kind='syllabus' WHERE kind='ib-syllabus'`.

## Phasing

- **Phase 1 — format**: extractors emit `kind` + `json-format.md` 1.1.0; consumer
  reads `doc["kind"]`; `syllabus` rename + its migration. Self-contained and
  cross-repo; ships value on its own (kinds are producer-stamped).
- **Phase 2 — primacy pointers**: schema columns + migration, set/read/clear the
  pointers, the Setup selector, bundle + export persistence.

## Decisions

Resolved: do **both** axes; primacy as **course-level pointers**.

To confirm while building:

- Final kind vocabulary: `ced` / `syllabus` / `book` (confirms the `ib-syllabus`
  rename, which drives the pill-class + migration). 
- Add `courses` to the export TSVs (recommended — also fixes title persistence) vs
  rely solely on bundles for course-level data.
- Official-outline selector now vs deferred (only matters with multiple outlines).

## Out of scope / follow-ups

- A full multiple-outlines workflow (beyond the pointer + default).
- Enabling SQLite FK enforcement globally (would let `ON DELETE SET NULL` replace
  the manual pointer-clearing) — separate, broader change.
- Per-`flavor`/`kind` validation that a loaded doc's kind is one the app knows
  (treat unknown kinds permissively, like unknown pill classes today).
