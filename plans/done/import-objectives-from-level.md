# Import objectives from a hierarchy level

## The idea

Some reference hierarchies have a level whose nodes **are** essentially raw
objectives — e.g. a CED's `essential-knowledge` or `learning-objective` level,
where each node's text is already a teachable statement. Today the only way to
get those into a course's objective pool is to hand-type them, paste a text/TSV
upload, or drag them in one by one. This feature lets a user pick **a hierarchy +
one of its levels** and, in one action:

1. For every node at that level, **create an objective from the node's text**.
2. **Intern** it into the course's objective pool (by `(course, text)` — the
   existing per-course natural key).
3. **Place** a coverage edge from that objective onto the very node it came from
   (so the objective is implicitly "placed into the corresponding node").

It is the inverse direction of `outline_import`: instead of pulling structure
*out* of a reference into the outline, it pulls a level *up* into the pool as
first-class objectives anchored back to their source nodes.

## The leaf-node worry — resolved

The user asked whether placing objectives on non-leaf nodes is "a db thing or
just a UI thing." **It is purely a UI thing.** Confirmed:

- `schema.sql` — `is_leaf` is a plain column (`-- 1 if the node has no children
  (the unit of coverage)`). There is **no** `CHECK`, trigger, or rule. The
  `coverage` foreign key references `nodes(course, hierarchy, node_id)`, i.e.
  *any* node.
- Every coverage-writing path (`place`, `node_objectives_bulk`,
  `hierarchy_upload` → `import_objectives.upsert`, `objective_new`) inserts
  without consulting `is_leaf`.
- `templates/workspace.html` (the reference view) gates only the **affordances**
  on `is_leaf`: the `+` button and the droppable `rawzone` render for leaves only.
  Crucially, non-leaf nodes that *already carry* objectives are still displayed —
  read-only:

  ```jinja
  {% elif n.objectives %}
    {# Higher-level nodes are not drop targets, but show any legacy objectives. #}
    <ul class="rawzone readonly">
      {% for o in n.objectives %}{% include "_rawitem.html" %}{% endfor %}
    </ul>
  {% endif %}
  ```

So objectives placed on a non-leaf node by this feature will **store, round-trip,
and display correctly**. The only behavioral consequences (both acceptable, see
*Non-goals / caveats*) are:

- The per-node bulk editor (`+`) won't be offered on those non-leaf nodes, so the
  imported objectives there are read-only in the reference view (they remain fully
  editable from the objectives table and draggable in the pool).
- `workspace_stats` / `leaf_status` count coverage only on **leaves**, so
  placements on non-leaf nodes don't move the "leaves covered / gaps" numbers.
  That's correct — covering an intermediate node isn't the same as covering each
  leaf beneath it.

No schema change, no `validate.py` change, no `coverage.tsv` change is required:
`read_course`, `write_course`, and `validate.validate_course` all treat coverage
uniformly regardless of the target node's depth.

## Data model recap (why this is a small change)

- `objectives(uuid, course, text, …)` — `UNIQUE(course, text)`; interned by
  `(course, text)`.
- `course_objectives(course, uuid, position)` — pool membership + order.
- `coverage(course, hierarchy, uuid, node_id, position)` — the objective↔node
  edge. For a reference hierarchy this is exactly what we want to write.
- `nodes(course, hierarchy, node_id, …, level, is_leaf, …, text)` — `level` is
  the per-hierarchy tag (`unit`, `topic`, `essential-knowledge`, …); `text` is the
  node's clean statement (duration tags already stripped on load).

`import_objectives.upsert(db_path, course, rows)` **already** does intern + pool +
place for `(uuid|None, text, hierarchy|None, node_id|None)` rows, idempotently,
checking node existence against the loaded `nodes`. This feature is essentially
"build those rows from the nodes at a chosen level, then call `upsert`."

## Implementation

### 1. `import_objectives.py` — a new function

Add a small function that turns a (hierarchy, level) selection into objective
rows and reuses `upsert`:

```python
def import_level(db_path, course, hierarchy, level):
    """Create+intern an objective from each node at `level` of reference
    `hierarchy`, and place each onto its own node. Idempotent (interns by
    (course, text); coverage INSERT OR IGNORE). Returns the upsert stats dict."""
    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn)
        nodes = conn.execute(
            "SELECT node_id, text FROM nodes WHERE course=? AND hierarchy=? AND level=?"
            " ORDER BY ordinal", (course, hierarchy, level)).fetchall()
    finally:
        conn.close()
    rows = [(None, (text or "").strip(), hierarchy, node_id)
            for node_id, text in nodes if (text or "").strip()]
    stats, dangling = upsert(db_path, course, rows)   # dangling is always {} here
    return stats
```

Notes:

- Reusing `upsert` gives idempotency, pool-append ordering, and the
  "replace placement in this hierarchy" semantics for free. Since each objective
  maps to exactly the node it came from, "replace" is a no-op on re-runs.
- Edge case: two nodes at the level with **identical text** intern to one
  objective placed on **both** nodes (a set per `(hierarchy, uuid)` in `upsert`).
  That's the correct interpretation (one objective, two homes) and is rare.
- `dangling` can't fire — node_ids come straight from `nodes`.
- Objective text = the node's full `text`. For the intended "raw objective"
  levels this is a single statement. (A `first_line_only` variant is possible
  later if a level's nodes carry bodies; not needed for v1.)

Optionally wire a `--from-level HIERARCHY:LEVEL` mode into `main()` for CLI/test
parity, mirroring how `--hierarchy` works. Low priority.

### 2. `app.py` — a new route

Add alongside `outline_import` (same validation shape):

```python
@app.route("/<course>/objectives/import-level", methods=["POST"])
def objectives_import_level(course):
    """Create+intern an objective from each node at a chosen level of a reference
    hierarchy, placing each onto its source node. See import_objectives.import_level."""
    hierarchy = (request.form.get("hierarchy") or "").strip()
    level = (request.form.get("level") or "").strip()
    with db() as conn:
        if not conn.execute(
                "SELECT 1 FROM hierarchies WHERE hierarchy=? AND course=? AND editable=0",
                (hierarchy, course)).fetchone():
            abort(404, f"no reference {hierarchy!r} for course {course!r}")
        has_level = conn.execute(
            "SELECT 1 FROM nodes WHERE course=? AND hierarchy=? AND level=? LIMIT 1",
            (course, hierarchy, level)).fetchone()
    if not has_level:
        flash("Pick a hierarchy level that has nodes.")
        return redirect(url_for("course_settings", course=course))
    stats = import_objectives.import_level(db_path(), course, hierarchy, level)
    apply_structural(course, f"Import {course}/{hierarchy} '{level}' nodes as objectives")
    flash(f"Imported the '{level}' level of {hierarchy}: "
          f"{stats['objectives_new']} new objective(s), "
          f"{stats['pooled']} added to the pool, {stats['placed']} placed.")
    return redirect(url_for("objectives", course=course))
```

`apply_structural` (used by the other importers) reifies to the course files so
the new objectives + coverage land in `objectives.tsv` / `coverage.tsv` and the
Save button can commit them — consistent with `hierarchy_upload` /
`objectives_import_from`.

### 3. `course_settings` route — supply per-reference levels

The Settings page already computes `references`. Extend each reference with its
levels (tag + count + node depth order) so the form can offer a level picker.
Reuse the existing `level_counts(nodes)` helper (shallowest-first, count + tag):

```python
ref_levels = {}
for h in references:
    rnodes = conn.execute(
        "SELECT node_id, parent_id, level FROM nodes WHERE course=? AND hierarchy=?",
        (course, h["hierarchy"])).fetchall()
    ref_levels[h["hierarchy"]] = level_counts(rnodes)   # [{count, label, tag}, …]
```

Pass `ref_levels` to the template.

### 4. `course_settings.html` — a new section

Add an "Add objectives from a hierarchy level" section modeled on "Rebuild the
outline". To avoid a build step / JS dependency between two selects, present a
**single select of `(hierarchy, level)` pairs**, each option labeled with its node
count:

```jinja
<h2>Add objectives from a hierarchy level</h2>
{% if ref_levels %}
<p>Turn every node at a chosen level of a reference hierarchy into a raw
   objective — interned into the pool and placed onto its source node. Useful when
   a reference has a level whose items are themselves objectives (e.g. essential
   knowledge). Re-running is safe: existing objectives are reused, not duplicated.</p>
<form method="post" action="{{ url_for('objectives_import_level', course=course) }}"
      class="caction" onsubmit="return this.pick.value && this.pick.value.includes('\x1f');">
  <label>Level:
    <select name="pick" required onchange="
        this.form.hierarchy.value = this.value.split('\x1f')[0];
        this.form.level.value = this.value.split('\x1f')[1];">
      <option value="" disabled selected>Choose a hierarchy level…</option>
      {% for h in references %}
        {% for lvl in ref_levels.get(h.hierarchy, []) %}
          <option value="{{ h.hierarchy }}\x1f{{ lvl.tag }}">
            {{ h.title or h.hierarchy }} → {{ lvl.label }} ({{ lvl.count }})
          </option>
        {% endfor %}
      {% endfor %}
    </select>
  </label>
  <input type="hidden" name="hierarchy"><input type="hidden" name="level">
  <button type="submit" class="btn"><i class="bi bi-list-check"></i> Import objectives</button>
</form>
{% endif %}
```

(Implementation detail: rather than the `\x1f` split-in-onchange shown above, the
cleaner version is two plain hidden fields populated by a tiny inline handler, or
two dependent `<select>`s wired with a few lines of JS — pick whichever matches
the surrounding style. The data contract for the route is just `hierarchy` +
`level`.)

Settings is the right home: it's where the analogous "Rebuild the outline" and
"Add a reference hierarchy" actions already live, and this is a deliberate,
occasional, course-shaping operation rather than an everyday edit.

### 5. Optional: a button on the reference workspace page

A nice follow-up (not required for v1): on a reference's own workspace page
(`hierarchy_view`), offer "Import the <level> level as objectives" per level in
the stat bar or a small menu, posting to the same route with that hierarchy
pre-filled. Defer until the Settings flow is proven.

## Tests

Add to a `test_import_objectives.py` (new) or extend an existing test:

- **Happy path**: load `examples/widgets` (or a fixture with a multi-level
  reference), call `import_objectives.import_level(db, course, ref, level)` for a
  **non-leaf** level, assert: an objective row exists per node (text matches),
  each is in `course_objectives`, and a `coverage` edge ties each objective to its
  source node.
- **Non-leaf placement sticks**: assert coverage rows land on nodes with
  `is_leaf=0` and that a subsequent `plan_io.write_course` →
  `plan_io.read_course` round-trip preserves them (the real-world worry).
- **Idempotency**: run twice; assert no duplicate objectives, pool rows, or
  coverage edges, and `objectives_new == 0` on the second run.
- **Empty/whitespace nodes** are skipped.

A fixture is needed where the target level is genuinely **non-leaf** (e.g.
`levels: unit, topic, learning-objective, essential-knowledge`, importing
`learning-objective`, which has `essential-knowledge` children). `examples/`
may need a small reference with such depth, or the test can synthesize one via
`load_nodes`.

## Non-goals / caveats

- **No provenance link.** Objectives intern by text, so if a source node's text
  is later edited in the reference and the level is re-imported, a *new* objective
  is minted and the old one is orphaned in the pool (its coverage edge stays on
  the node, pointing at the old uuid). This matches every other text-interning
  importer; a provenance column is out of scope.
- **Read-only in the reference view** for non-leaf placements (the `+` bulk
  editor stays leaf-only). Intentional — see *The leaf-node worry*. If editing
  imported objectives *in place on a non-leaf node* becomes desirable, that's a
  separate, larger change to `workspace.html` (offer the `rawzone` on non-leaves)
  and is explicitly not part of this feature.
- **Leaf-coverage stats** are unaffected by non-leaf placements, by design.
- No schema, `coverage.tsv`, or `validate.py` changes.

## Files touched

- `import_objectives.py` — add `import_level` (+ optional CLI flag).
- `app.py` — add `objectives_import_level` route; extend `course_settings` to
  pass `ref_levels`.
- `templates/course_settings.html` — add the "Add objectives from a hierarchy
  level" section.
- `test_import_objectives.py` — new tests (happy path, non-leaf round-trip,
  idempotency).
- `CLAUDE.md` / `FORMAT.md` — no change required (no format change); optionally a
  one-line mention of the new Settings action.
