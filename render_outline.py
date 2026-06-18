"""Render a course's plan from the lesson-planning database to markdown.

The deliverable: Units -> Lessons (each with its title, learning objective, and
the raw objectives placed in it, with their CED nodes), a unit "rough" area for
raws not yet in a lesson, plus a traceability appendix (every leaf node -> where
it's covered: a lesson, a unit rough-cut, or a gap) and a gap list. Proof that
the teacher's own plan covers the official outline.

    uv run render_outline.py lesson-planning/db.db csa/lesson-plan.md --course csa
"""

import argparse
import sqlite3


def fetch(conn, course):
    conn.row_factory = sqlite3.Row
    coverage = {}
    for r in conn.execute("SELECT uuid, node_id FROM coverage WHERE hierarchy=?", (course,)):
        coverage.setdefault(r["uuid"], []).append(r["node_id"])

    raws = {}
    for r in conn.execute(
        """SELECT o.uuid, o.text, co.plan_unit, co.plan_lesson
             FROM objectives o
             JOIN course_objectives co ON co.uuid=o.uuid AND co.course=?
            WHERE o.status='active'""", (course,)):
        raws[r["uuid"]] = {"uuid": r["uuid"], "text": r["text"],
                           "nodes": sorted(coverage.get(r["uuid"], [])),
                           "plan_unit": r["plan_unit"], "plan_lesson": r["plan_lesson"]}

    lessons = [{"uuid": r["uuid"], "unit_id": r["unit_id"], "title": r["title"],
                "lo": r["learning_objective"]} for r in conn.execute(
                    "SELECT uuid, unit_id, title, learning_objective FROM lessons "
                    "WHERE course=? ORDER BY position, uuid", (course,))]
    units = [{"uuid": r["uuid"], "title": r["title"]} for r in conn.execute(
        "SELECT uuid, title FROM units WHERE course=? ORDER BY position, uuid", (course,))]

    by_lesson, rough_by_unit = {}, {}
    for o in sorted(raws.values(), key=lambda o: o["text"].lower()):
        if o["plan_lesson"]:
            by_lesson.setdefault(o["plan_lesson"], []).append(o)
        elif o["plan_unit"]:
            rough_by_unit.setdefault(o["plan_unit"], []).append(o)
    for L in lessons:
        L["raws"] = by_lesson.get(L["uuid"], [])
    lessons_by_unit = {}
    for L in lessons:
        lessons_by_unit.setdefault(L["unit_id"], []).append(L)
    for u in units:
        u["lessons"] = lessons_by_unit.get(u["uuid"], [])
        u["rough"] = rough_by_unit.get(u["uuid"], [])
    unassigned = lessons_by_unit.get(None, [])

    leaves = [{"node_id": r["node_id"], "text": (r["text"] or "").split("\n", 1)[0]}
              for r in conn.execute("SELECT node_id, text FROM nodes "
                                    "WHERE hierarchy=? AND is_leaf=1 ORDER BY ordinal", (course,))]
    covered_any = {r["node_id"] for r in conn.execute(
        """SELECT DISTINCT cv.node_id FROM coverage cv
             JOIN objectives o ON o.uuid=cv.uuid AND o.status='active'
            WHERE cv.course=?""", (course,))}
    return units, unassigned, leaves, covered_any, raws


def render(course, units, unassigned, leaves, covered_any, raws):
    all_lessons = [L for u in units for L in u["lessons"]] + unassigned
    for i, L in enumerate(all_lessons, 1):
        L["num"] = i

    lesson_of, unit_of = {}, {}
    for u in units:
        for o in u["rough"]:
            unit_of[o["uuid"]] = u
        for L in u["lessons"]:
            for o in L["raws"]:
                lesson_of[o["uuid"]] = L
    for L in unassigned:
        for o in L["raws"]:
            lesson_of[o["uuid"]] = L

    node_raws = {}
    for uuid, o in raws.items():
        for n in o["nodes"]:
            node_raws.setdefault(n, []).append(uuid)

    def leaf_label(node_id):
        coverers = node_raws.get(node_id, [])
        hit = []
        for u in coverers:
            L = lesson_of.get(u)
            if L and f"Lesson {L['num']}" not in hit:
                hit.append(f"Lesson {L['num']}")
        if hit:
            return "; ".join(hit)
        rough = sorted({unit_of[u]["title"] for u in coverers if u in unit_of})
        if rough:
            return "rough: " + ", ".join(f'Unit "{t}"' for t in rough)
        return "_objective only, not placed_" if coverers else "**GAP**"

    planned = sum(1 for lf in leaves
                  if any(u in lesson_of for u in node_raws.get(lf["node_id"], [])))
    gaps = [lf for lf in leaves if lf["node_id"] not in covered_any]
    placed = sum(1 for o in raws.values() if o["plan_unit"] or o["plan_lesson"])
    n_lo = sum(1 for L in all_lessons if L["lo"])

    out = [f"# {course.upper()} plan", ""]
    out.append(
        f"_{len(units)} units · {len(all_lessons)} lessons "
        f"({n_lo} with a learning objective) · {placed}/{len(raws)} raw objectives "
        f"placed · {planned}/{len(leaves)} leaves planned "
        f"({round(100 * planned / len(leaves)) if leaves else 0}%) · {len(gaps)} gaps._")
    out.append("")

    def emit_raw(o):
        tag = f"  (`{'`, `'.join(o['nodes'])}`)" if o["nodes"] else ""
        out.append(f"- {o['text']}{tag}")

    groups = [(u["title"], u["lessons"], u["rough"]) for u in units]
    if unassigned:
        groups.append((None, unassigned, []))
    for title, lessons, rough in groups:
        out.append(f"## Unit: {title}" if title is not None else "## (Unassigned lessons)")
        out.append("")
        if rough:
            out.append("**Rough — not yet in a lesson:**")
            out.append("")
            for o in rough:
                emit_raw(o)
            out.append("")
        for L in lessons:
            head = f"Lesson {L['num']}" + (f": {L['title']}" if L["title"] else "")
            out.append(f"### {head}")
            out.append("")
            out.append(f"**Learning objective:** {L['lo']}" if L["lo"]
                       else "_(no learning objective yet)_")
            out.append("")
            for o in L["raws"]:
                emit_raw(o)
            if L["raws"]:
                out.append("")

    unplaced = [o for o in raws.values() if not o["plan_unit"] and not o["plan_lesson"]]
    if unplaced:
        out.append("## Unplaced raw objectives")
        out.append("")
        for o in sorted(unplaced, key=lambda o: o["text"].lower()):
            emit_raw(o)
        out.append("")

    out.append("## Traceability — leaf coverage")
    out.append("")
    for lf in leaves:
        out.append(f"- `{lf['node_id']}` — {leaf_label(lf['node_id'])}")
    out.append("")

    out.append(f"## Gaps — {len(gaps)} leaves with no objective")
    out.append("")
    for lf in gaps:
        out.append(f"- `{lf['node_id']}` — {lf['text']}")
    out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("database")
    p.add_argument("output", help="markdown file to write")
    p.add_argument("--course", default="csa")
    args = p.parse_args()
    conn = sqlite3.connect(args.database)
    try:
        data = fetch(conn, args.course)
    finally:
        conn.close()
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(render(args.course, *data))
    units, unassigned, leaves, covered_any, raws = data
    n_lessons = sum(len(u["lessons"]) for u in units) + len(unassigned)
    print(f"wrote {args.output}: {len(units)} units, {n_lessons} lessons")


if __name__ == "__main__":
    main()
