"""Validate a course directory's internal consistency, on the raw files.

Two invariants a human editor -- or a buggy older writer -- can break (the bug
that prompted this wrote outline node ids where UUIDs belonged):

  1. Every place that must hold a UUID actually holds one: each row of
     ``objectives.tsv`` (``uuid``) and ``coverage.tsv`` (``uuid``), and every
     ``lessons/*.md`` front-matter ``uuid:``.

  2. Every UUID reference resolves to something that exists: a coverage row's
     ``uuid`` to an objective; a ``plan.md`` objective-bullet token to an
     objective; a ``plan.md`` lesson-heading token to a lesson file; and a
     coverage row's ``(hierarchy_id, node_id)`` to a node of that reference
     hierarchy.

``validate_course(course_dir)`` returns a list of human-readable problem strings
(empty == clean). It reads the files only -- no database -- so it sees the on-disk
state *before* ``read_course``'s lenient resolution papers over a dangling token
by minting a fresh objective/lesson. ``read_course`` calls it on load and prints
any problems as warnings; ``uv run validate.py <courses-dir>`` checks a whole tree
(non-zero exit if anything is wrong).
"""

import csv
import os
import re
import sys

import hierarchy
import load_nodes

# Canonical uuid4 string form (8-4-4-4-12 hex). A node id ("1.2", "CRD-1.A", ...)
# -- the corruption we guard against -- never matches this.
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_uuid(s):
    return bool(s) and bool(UUID_RE.match(s))


def _short(s, n=60):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _tsv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _resolve(token, sorted_uuids):
    """The uuids in `sorted_uuids` whose value starts with `token` (lower-cased,
    like plan_io.resolve_token), so 0 / 1 / >1 can be reported distinctly."""
    t = token.lower()
    return [u for u in sorted_uuids if u.startswith(t)]


def validate_course(course_dir):
    """Return a list of internal-consistency problems for one course directory
    (empty == clean). Never raises for content problems -- it collects them; it can
    still raise for an unreadable directory."""
    import plan_io   # local: plan_io imports this module

    problems = []
    p = problems.append

    # Classify the top-level .md files: the plan is the one with a `course:` key.
    plan_path, ref_paths = None, []
    for fn in plan_io._md_files(course_dir):
        path = os.path.join(course_dir, fn)
        with open(path, encoding="utf-8") as f:
            meta, _ = hierarchy.parse_front_matter(f.read())
        if "course" in meta:
            plan_path = path
        else:
            ref_paths.append(path)
    if plan_path is None:
        p("no plan.md (a .md with a 'course:' front-matter key)")
        return problems

    # (1) objectives.tsv: every uuid is a real UUID. These are the objectives every
    # other uuid reference must point at.
    obj_uuids = set()
    for i, r in enumerate(_tsv_rows(os.path.join(course_dir, plan_io.OBJECTIVES_TSV)),
                          start=2):   # row 1 is the header
        uuid = (r.get("uuid") or "").strip()
        if not uuid:
            p(f"objectives.tsv line {i}: empty uuid")
        elif not is_uuid(uuid):
            p(f"objectives.tsv line {i}: uuid {uuid!r} is not a UUID")
        elif uuid in obj_uuids:
            p(f"objectives.tsv line {i}: duplicate uuid {uuid}")
        else:
            obj_uuids.add(uuid)

    # Reference hierarchies: slug -> set(node_id), so coverage node_ids can be
    # checked for existence (these node ids are NOT uuids -- the reference's own ids).
    ref_nodes = {}
    for path in ref_paths:
        fn = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                doc = load_nodes.parse(f.read())
        except (Exception, SystemExit) as e:
            p(f"{fn}: cannot parse reference hierarchy ({e})")
            continue
        slug = doc.get("slug") or os.path.splitext(fn)[0]
        ref_nodes[slug] = {n["id"] for n in doc["nodes"]}

    # (1) lessons/*.md: every front-matter uuid is a real UUID.
    lesson_uuids = set()
    ldir = os.path.join(course_dir, plan_io.LESSONS_DIR)
    if os.path.isdir(ldir):
        for fn in sorted(os.listdir(ldir)):
            path = os.path.join(ldir, fn)
            if not (fn.endswith(".md") and os.path.isfile(path)):
                continue
            with open(path, encoding="utf-8") as f:
                meta, _ = hierarchy.parse_front_matter(f.read())
            uuid = (meta.get("uuid") or "").strip()
            if not uuid:
                p(f"lessons/{fn}: no 'uuid:' front matter")
            elif not is_uuid(uuid):
                p(f"lessons/{fn}: uuid {uuid!r} is not a UUID")
            elif uuid in lesson_uuids:
                p(f"lessons/{fn}: duplicate lesson uuid {uuid}")
            else:
                lesson_uuids.add(uuid)

    # (1)+(2) coverage.tsv: uuid is a UUID and names an objective; (hierarchy_id,
    # node_id) names an existing reference node.
    for i, r in enumerate(_tsv_rows(os.path.join(course_dir, plan_io.COVERAGE_TSV)),
                          start=2):
        uuid = (r.get("uuid") or "").strip()
        hid = (r.get("hierarchy_id") or "").strip()
        nid = (r.get("node_id") or "").strip()
        if not is_uuid(uuid):
            p(f"coverage.tsv line {i}: uuid {uuid!r} is not a UUID")
        elif uuid not in obj_uuids:
            p(f"coverage.tsv line {i}: uuid {uuid} has no objective in objectives.tsv")
        if hid not in ref_nodes:
            p(f"coverage.tsv line {i}: hierarchy_id {hid!r} is not a reference hierarchy")
        elif nid not in ref_nodes[hid]:
            p(f"coverage.tsv line {i}: node_id {nid!r} not in hierarchy {hid!r}")

    # (2) plan.md tokens: each lesson-heading token resolves to one lesson file and
    # each objective-bullet token to one objective (by shortest-unique prefix, the
    # same matching read_course uses). A tokenless heading/bullet is a hand-added
    # new lesson/objective (no identity yet), not corruption -- skip it.
    with open(plan_path, encoding="utf-8") as f:
        _meta, _units, lessons, _los, bullets = plan_io.parse_plan(f.read())
    obj_sorted = sorted(obj_uuids)
    lesson_sorted = sorted(lesson_uuids)
    for _key, _parent, token, title, _dur in lessons:
        if token is None:
            continue
        m = _resolve(token, lesson_sorted)
        if not m:
            p(f"plan.md lesson {_short(title)!r}: token (#{token}) matches no lesson file")
        elif len(m) > 1:
            p(f"plan.md lesson {_short(title)!r}: token (#{token}) is ambiguous "
              f"({len(m)} lesson files)")
    for text, token, _placement in bullets:
        if token is None:
            continue
        m = _resolve(token, obj_sorted)
        if not m:
            p(f"plan.md objective {_short(text)!r}: token (#{token}) matches no "
              "objective in objectives.tsv")
        elif len(m) > 1:
            p(f"plan.md objective {_short(text)!r}: token (#{token}) is ambiguous "
              f"({len(m)} objectives)")

    return problems


def main(argv=None):
    import seed   # course_dirs(): a courses directory's course subdirectories
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: validate.py <courses-dir>", file=sys.stderr)
        return 2
    root = argv[0]
    dirs = seed.course_dirs(root)
    if not dirs:
        # Maybe a single course directory was passed directly.
        if os.path.exists(os.path.join(root, "plan.md")):
            dirs = [root]
        else:
            print(f"validate: no course directories in {root!r}", file=sys.stderr)
            return 2
    total = 0
    for cd in dirs:
        problems = validate_course(cd)
        name = os.path.basename(os.path.normpath(cd))
        if problems:
            total += len(problems)
            print(f"{name}: {len(problems)} problem(s)")
            for prob in problems:
                print(f"  - {prob}")
        else:
            print(f"{name}: ok")
    if total:
        print(f"\n{total} problem(s) found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
