# lesson-planning

A small, course-agnostic tool for turning a curriculum **hierarchy** (units →
topics → objectives → knowledge statements, or whatever shape your standard
uses) into a **traceable lesson plan**. You bring your own hierarchy and your own
learning objectives; the tool helps you place objectives under the standard,
shows you what's covered and what's still a gap, and renders the finished plan as
markdown.

Nothing about any particular course is baked in. Point it at *your* hierarchy
and *your* objectives.

Hierarchies are ingested as **node-list JSON** — the cross-repo data contract
emitted by the companion [hierarchy-extractors](../hierarchy-extractors) repo's
`build_hierarchy_json.py` from a hierarchy markdown file. This tool does no
markdown parsing itself: it's a dumb loader of that JSON (see
`hierarchy-extractors/json-format.md` for the format).

## What you get

- A web app (Flask) to load a hierarchy, place raw objectives onto it, author
  lessons, and watch coverage fill in.
- A command-line pipeline for the same lifecycle: load a hierarchy, import
  objectives, snapshot to git-diffable TSVs, and render the plan.
- A **traceability** view: every leaf of the standard maps to the lesson(s) that
  cover it, plus an explicit list of gaps.

The only third-party dependency is **Flask**. Requires Python ≥ 3.13; scripts run
with [`uv`](https://docs.astral.sh/uv/).

## Quick start

A synthetic example course ("Intro to Widgets") lives in `examples/`. Use it to
see the whole pipeline end to end:

```bash
# 1. Load your hierarchy node-list JSON into a fresh database (creates the course).
uv run load_nodes.py examples/widgets-hierarchy.json db.db \
    --course widgets --hierarchy widgets-ced --course-title "Intro to Widgets"

# 2. Import objectives (TSV: an `objective` column, optional `node_id`/`uuid`).
uv run import_objectives.py examples/objectives.tsv db.db --course widgets

# 3. Render the plan (units → lessons, traceability appendix, gap list).
uv run render_outline.py db.db /tmp/plan.md --course widgets
```

Or do it all in the browser:

```bash
uv run app.py          # http://localhost:5001
```

The app boots an empty database from `schema.sql` on first run. On the **Data**
page, upload a hierarchy node-list JSON file (e.g. `examples/widgets-hierarchy.json`)
to create a course, then seed objectives from the **Objectives** page and plan from
each course's workspace.

## The hierarchy format

A hierarchy is authored as a nested markdown outline and converted to node-list
JSON by the hierarchy-extractors repo. `examples/widgets-hierarchy.md` is the
authoring source; `examples/widgets-hierarchy.json` is the loadable artifact (what
the commands above ingest). The markdown's level-1 heading names the top level and
the flavor, and deeper headings carry a verbatim id as their first token:

```markdown
# Unit 1: Widget Basics
## 1.1 What Is a Widget
### 1.1.A Describe the parts of a widget
#### 1.1.A.1 A widget has a frobnicator and a sprocket.
```

Convert it to JSON with the extractor, then load that:

```bash
uv run ../hierarchy-extractors/build_hierarchy_json.py \
    examples/widgets-hierarchy.md examples/widgets-hierarchy.json
```

The deepest nodes (here, the `####` knowledge statements) are the **leaves** —
the unit of coverage. Your lesson plan "covers" the standard when every leaf maps
to a lesson.

## Saving & version control

`db.db` is the live working copy and is gitignored. The committed state is the
`export/` directory of TSV snapshots:

```bash
uv run export_planning.py db.db export/                  # snapshot
uv run import_planning.py db.db export/                  # restore
uv run rebuild_db.py examples/widgets-hierarchy.json     # rebuild from scratch
```

(The app exposes the same export/restore on its **Data** page.)
