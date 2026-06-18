"""Render a course's lesson plan from the lesson-planning database to markdown.

The deliverable: an ordered list of lessons, each with its learning objectives and
the raw objectives (and CED nodes) they roll up, followed by a traceability
appendix (every leaf node -> the lesson(s) that cover it) and a gap list. This is
the proof that the teacher's own lesson structure still covers the whole official
outline.

    uv run render_outline.py lesson-planning/db.db csa/lesson-plan.md --course csa
"""

import argparse
import sqlite3


def fetch(conn, course):
    conn.row_factory = sqlite3.Row
    coverage = {}
    for r in conn.execute("SELECT uuid, node_id FROM coverage WHERE course=?", (course,)):
        coverage.setdefault(r["uuid"], []).append(r["node_id"])

    def raws_of(lo_id):
        rows = conn.execute(
            """SELECT o.uuid, o.text FROM objective_rollup r
                 JOIN objectives o ON o.uuid = r.objective_uuid
                WHERE r.lesson_objective_id = ? ORDER BY o.text""", (lo_id,))
        return [{"uuid": r["uuid"], "text": r["text"],
                 "nodes": sorted(coverage.get(r["uuid"], []))} for r in rows]

    def los_where(clause, *params):
        out = []
        for lo in conn.execute(
            f"SELECT id, text FROM lesson_objectives WHERE {clause} "
            "ORDER BY position IS NULL, position, id", params):
            raws = raws_of(lo["id"])
            nodes = sorted({n for r in raws for n in r["nodes"]})
            out.append({"id": lo["id"], "text": lo["text"], "raws": raws, "nodes": nodes})
        return out

    lessons_by_unit, all_lessons = {}, []
    for L in conn.execute("SELECT id, title, unit_id FROM lessons WHERE course=? "
                          "ORDER BY position, id", (course,)):
        lesson = {"id": L["id"], "title": L["title"],
                  "objectives": los_where("lesson_id=?", L["id"])}
        lessons_by_unit.setdefault(L["unit_id"], []).append(lesson)
        all_lessons.append(lesson)
    units = [{"title": u["title"], "lessons": lessons_by_unit.get(u["id"], [])}
             for u in conn.execute("SELECT id, title FROM units WHERE course=? "
                                   "ORDER BY position, id", (course,))]
    ungrouped = lessons_by_unit.get(None, [])

    unscheduled = los_where("course=? AND lesson_id IS NULL", course)
    leaves = [{"node_id": r["node_id"], "text": (r["text"] or "").split("\n", 1)[0]}
              for r in conn.execute("SELECT node_id, text FROM nodes "
                                    "WHERE course=? AND is_leaf=1 ORDER BY ordinal", (course,))]
    covered_any = {r["node_id"] for r in conn.execute(
        """SELECT DISTINCT cv.node_id FROM coverage cv
             JOIN objectives o ON o.uuid=cv.uuid AND o.status='active'
            WHERE cv.course=?""", (course,))}
    return units, ungrouped, all_lessons, unscheduled, leaves, covered_any


def render(course, units, ungrouped, all_lessons, unscheduled, leaves, covered_any):
    # Global lesson numbering in document order (units, then unassigned).
    leaf_lessons = {}
    for i, L in enumerate(all_lessons, 1):
        L["num"] = i
        label = f"Lesson {i}"
        for lo in L["objectives"]:
            for n in lo["nodes"]:
                leaf_lessons.setdefault(n, [])
                if label not in leaf_lessons[n]:
                    leaf_lessons[n].append(label)

    planned = sum(1 for lf in leaves if lf["node_id"] in leaf_lessons)
    gaps = [lf for lf in leaves if lf["node_id"] not in covered_any]
    n_lo = sum(len(L["objectives"]) for L in all_lessons) + len(unscheduled)

    out = [f"# {course.upper()} lesson plan", ""]
    out.append(
        f"_{len(units)} units · {len(all_lessons)} lessons · {n_lo} learning "
        f"objectives · {planned}/{len(leaves)} leaves planned "
        f"({round(100 * planned / len(leaves)) if leaves else 0}%) · "
        f"{len(gaps)} gaps · {len(unscheduled)} unscheduled learning objectives._")
    out.append("")

    groups = [(u["title"], u["lessons"]) for u in units]
    if ungrouped:
        groups.append((None, ungrouped))
    for title, lessons in groups:
        out.append(f"## Unit: {title}" if title is not None else "## (Unassigned lessons)")
        out.append("")
        if not lessons:
            out.append("_(no lessons yet)_")
            out.append("")
        for L in lessons:
            out.append(f"### Lesson {L['num']}: {L['title']}")
            out.append("")
            if not L["objectives"]:
                out.append("_(no learning objectives yet)_")
                out.append("")
            for lo in L["objectives"]:
                out.append(f"#### {lo['text']}")
                out.append("")
                if lo["nodes"]:
                    out.append("Covers: " + ", ".join(f"`{n}`" for n in lo["nodes"]))
                    out.append("")
                if lo["raws"]:
                    out.append("Rolls up:")
                    out.append("")
                    for r in lo["raws"]:
                        tag = f"  (`{'`, `'.join(r['nodes'])}`)" if r["nodes"] else ""
                        out.append(f"- {r['text']}{tag}")
                    out.append("")

    if unscheduled:
        out.append("## Unscheduled learning objectives")
        out.append("")
        for lo in unscheduled:
            tag = f"  (covers `{'`, `'.join(lo['nodes'])}`)" if lo["nodes"] else ""
            out.append(f"- {lo['text']}{tag}")
        out.append("")

    out.append("## Traceability — leaf coverage")
    out.append("")
    for lf in leaves:
        where = leaf_lessons.get(lf["node_id"])
        mark = "; ".join(where) if where else ("**GAP**" if lf["node_id"] not in covered_any
                                               else "_objective only, not scheduled_")
        out.append(f"- `{lf['node_id']}` — {mark}")
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
    md = render(args.course, *data)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(md)
    units, ungrouped, all_lessons, unscheduled, leaves, covered_any = data
    print(f"wrote {args.output}: {len(units)} units, {len(all_lessons)} lessons, "
          f"{len(unscheduled)} unscheduled learning objectives")


if __name__ == "__main__":
    main()
