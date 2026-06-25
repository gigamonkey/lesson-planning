"""Export/import a whole course as a single self-contained JSON bundle.

A bundle captures everything needed to recreate one course: the course row, all
its hierarchies (reference + outline) with their nodes and per-node attrs, its
objectives (text + pool membership/order) and the coverage edges into its
hierarchies, plus its outline<->reference targets. Import recreates it all in one
transaction.

This is additive to the markdown corpus (plan_io.py / seed.py / rebuild_db.py):
the corpus -- a directory of course directories of markdown + TSVs -- is the
git-diffable committed state; a bundle makes a single course portable as one
self-contained file (move it between databases, or delete-and-restore one course).

    uv run course_bundle.py export db.db <course> [out.json]
    uv run course_bundle.py import db.db <bundle.json> [--as <course>]
"""

import argparse
import json
import sqlite3

BUNDLE_VERSION = "1.2.0"   # 1.1.0: coverage.position; 1.2.0: node_duration + course calendar
FORMAT_MAJOR = 1


def export_course(conn, course):
    """Return a bundle dict for `course`. Raises KeyError if the course is absent."""
    conn.row_factory = sqlite3.Row
    crow = conn.execute("SELECT course, title, primary_outline, calendar "
                        "FROM courses WHERE course=?", (course,)).fetchone()
    if not crow:
        raise KeyError(course)

    hierarchies = []
    for h in conn.execute(
        "SELECT hierarchy, editable, title, source, source_md FROM hierarchies "
        "WHERE course=? ORDER BY editable, hierarchy", (course,)):
        nodes = [dict(r) for r in conn.execute(
            "SELECT node_id, parent_id, level, is_leaf, ordinal, text FROM nodes "
            "WHERE course=? AND hierarchy=? ORDER BY ordinal, node_id", (course, h["hierarchy"]))]
        attrs = [dict(r) for r in conn.execute(
            "SELECT node_id, name, value FROM node_attr "
            "WHERE course=? AND hierarchy=? ORDER BY node_id, name", (course, h["hierarchy"]))]
        durations = [dict(r) for r in conn.execute(
            "SELECT node_id, amount, unit FROM node_duration "
            "WHERE course=? AND hierarchy=? ORDER BY node_id", (course, h["hierarchy"]))]
        hierarchies.append({"hierarchy": h["hierarchy"],
                            "editable": h["editable"], "title": h["title"],
                            "source": h["source"], "source_md": h["source_md"],
                            "nodes": nodes, "node_attr": attrs,
                            "node_duration": durations})

    objectives = [dict(r) for r in conn.execute(
        "SELECT o.uuid, o.text, o.status, co.position FROM objectives o "
        "JOIN course_objectives co ON co.uuid=o.uuid AND co.course=? "
        "ORDER BY co.position, o.text", (course,))]

    coverage = [dict(r) for r in conn.execute(
        "SELECT hierarchy, uuid, node_id, position FROM coverage WHERE course=? "
        "ORDER BY hierarchy, node_id, position, uuid", (course,))]

    targets = [dict(r) for r in conn.execute(
        "SELECT outline, reference, position FROM hierarchy_targets WHERE course=? "
        "ORDER BY position, reference", (course,))]

    return {"version": BUNDLE_VERSION,
            "course": {"course": crow["course"], "title": crow["title"],
                       "primary_outline": crow["primary_outline"],
                       "calendar": crow["calendar"]},
            "hierarchies": hierarchies, "objectives": objectives,
            "coverage": coverage, "hierarchy_targets": targets}


def import_course(conn, doc, course=None):
    """Recreate a course from a bundle dict. `course` overrides the bundle's id.

    Raises ValueError on an unsupported version, an existing course id, or a
    hierarchy-slug collision with another course. Interns objectives by uuid then
    by text (so shared objectives aren't duplicated), remapping coverage edges to
    the surviving uuid. Returns the created course id.
    """
    major = str(doc.get("version", "")).split(".")[0]
    if not major.isdigit() or int(major) != FORMAT_MAJOR:
        raise ValueError(f"unsupported bundle version {doc.get('version')!r} "
                         f"(this app handles major {FORMAT_MAJOR})")
    cid = course or doc["course"]["course"]
    if conn.execute("SELECT 1 FROM courses WHERE course=?", (cid,)).fetchone():
        raise ValueError(f"course {cid!r} already exists")
    # Slugs are course-relative now, so a fresh course can't collide with another.

    conn.execute("INSERT INTO courses(course, title) VALUES (?, ?)",
                 (cid, doc["course"]["title"]))
    for h in doc["hierarchies"]:
        conn.execute("INSERT INTO hierarchies(course, hierarchy, editable, title,"
                     " source, source_md) VALUES (?, ?, ?, ?, ?, ?)",
                     (cid, h["hierarchy"], h["editable"], h["title"],
                      h.get("source"), h.get("source_md")))
        conn.executemany(
            "INSERT INTO nodes(course, hierarchy, node_id, parent_id, level, is_leaf, ordinal, text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(cid, h["hierarchy"], n["node_id"], n["parent_id"], n["level"], n["is_leaf"],
              n["ordinal"], n["text"]) for n in h["nodes"]])
        conn.executemany(
            "INSERT INTO node_attr(course, hierarchy, node_id, name, value) VALUES (?, ?, ?, ?, ?)",
            [(cid, h["hierarchy"], a["node_id"], a["name"], a["value"])
             for a in h.get("node_attr", [])])
        conn.executemany(
            "INSERT INTO node_duration(course, hierarchy, node_id, amount, unit)"
            " VALUES (?, ?, ?, ?, ?)",
            [(cid, h["hierarchy"], d["node_id"], d["amount"], d["unit"])
             for d in h.get("node_duration", [])])

    # Restore the course's outline pointer + calendar binding now that its
    # hierarchies exist (slugs are carried verbatim, valid under a new id too).
    conn.execute("UPDATE courses SET primary_outline=?, calendar=? WHERE course=?",
                 (doc["course"].get("primary_outline"), doc["course"].get("calendar"), cid))

    uuid_map = {}
    for o in doc["objectives"]:
        if conn.execute("SELECT 1 FROM objectives WHERE uuid=?", (o["uuid"],)).fetchone():
            uid = o["uuid"]
        else:
            trow = conn.execute("SELECT uuid FROM objectives WHERE text=?",
                                (o["text"],)).fetchone()
            if trow:
                uid = trow[0]
            else:
                conn.execute("INSERT INTO objectives(uuid, text, status) VALUES (?, ?, ?)",
                             (o["uuid"], o["text"], o.get("status") or "active"))
                uid = o["uuid"]
        uuid_map[o["uuid"]] = uid
        conn.execute("INSERT OR IGNORE INTO course_objectives(course, uuid, position)"
                     " VALUES (?, ?, ?)", (cid, uid, o.get("position")))

    for cv in doc["coverage"]:
        conn.execute("INSERT OR IGNORE INTO coverage(course, hierarchy, uuid, node_id, position)"
                     " VALUES (?, ?, ?, ?, ?)",
                     (cid, cv["hierarchy"], uuid_map.get(cv["uuid"], cv["uuid"]), cv["node_id"],
                      cv.get("position")))
    for t in doc["hierarchy_targets"]:
        conn.execute("INSERT OR IGNORE INTO hierarchy_targets(course, outline, reference, position)"
                     " VALUES (?, ?, ?, ?)", (cid, t["outline"], t["reference"], t.get("position")))
    return cid


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("export", help="write a course bundle")
    pe.add_argument("database")
    pe.add_argument("course")
    pe.add_argument("output", nargs="?", help="JSON file (default: stdout)")
    pi = sub.add_parser("import", help="recreate a course from a bundle")
    pi.add_argument("database")
    pi.add_argument("bundle")
    pi.add_argument("--as", dest="as_course", help="import under this course id")
    args = p.parse_args()

    conn = sqlite3.connect(args.database)
    try:
        if args.cmd == "export":
            doc = export_course(conn, args.course)
            text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                with open(args.output, "w") as f:
                    f.write(text)
                print(f"wrote {args.output}: course {args.course!r}, "
                      f"{len(doc['hierarchies'])} hierarchies, {len(doc['objectives'])} objectives")
            else:
                print(text, end="")
        else:
            with open(args.bundle) as f:
                doc = json.load(f)
            cid = import_course(conn, doc, course=args.as_course)
            conn.commit()
            print(f"imported course {cid!r} from {args.bundle}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
