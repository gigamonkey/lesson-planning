"""Parse a curriculum/book hierarchy markdown file into a flat list of sections.

Shared by build_hierarchy_xml.py and build_hierarchy_db.py. The flavor is
detected from the first level-1 heading; sections carry their ids verbatim (e.g.
"1", "1.1", "1.1.A", "1.1.A.1") and consumers apply their own id transformations.

Flavors and their per-level tags:

- csp:    big-idea / essential-understanding / learning-objective / essential-knowledge
- csa:    unit / topic / learning-objective / essential-knowledge
- ib:     theme / topic / subtopic / learning-statement / content
- book:   chapter / section / subsection
- course: unit / lesson / objective

The `course` flavor shares csa's level-1 heading (`# Unit N: TITLE`), so the two
are told apart by heading *depth* (see detect_flavor): csa nests headings down to
level 3-4 (`### 1.1.A`, `#### 1.1.A.1`), while course stops at level-2 lesson
headings (`## N.1 TITLE`) and lists its level-3 raw objectives as a markdown
bulleted list (`- …`) instead of `###` headings. Bullet objectives have no
authored id, so one is synthesized as the lesson id plus a sequential number
(lesson `1.1` -> `1.1.1`, `1.1.2`, …), like IB content.
"""

import re
import sys

HEADING = re.compile(r"^(#{1,5}) (.+)$")
BIG_IDEA = re.compile(r"^Big Idea \d+: (.+) \((\w+)\)$")
UNIT = re.compile(r"^Unit (\d+): (.+)$")
THEME = re.compile(r"^Theme ([AB]): (.+)$")
CHAPTER = re.compile(r"^Chapter (\d+): (.+)$")
# A course-flavor raw objective: a top-level (column-0) markdown bullet.
OBJECTIVE_BULLET = re.compile(r"^[-*] +(.+)$")

LEVEL_TAGS = {
    "csp": {
        1: "big-idea",
        2: "essential-understanding",
        3: "learning-objective",
        4: "essential-knowledge",
    },
    "csa": {
        1: "unit",
        2: "topic",
        3: "learning-objective",
        4: "essential-knowledge",
    },
    "ib": {
        1: "theme",
        2: "topic",
        3: "subtopic",
        4: "learning-statement",
        5: "content",
    },
    "book": {1: "chapter", 2: "section", 3: "subsection"},
    "course": {1: "unit", 2: "lesson", 3: "objective"},
}

# A hierarchy's "kind" — the type of document, distinct from but defaulting to a
# function of the flavor (the CSA and CSP CEDs are both "ced", so kind is not 1:1
# with flavor). Overridable per file via the `kind:` front-matter key.
FLAVOR_KIND = {
    "csa": "ced",
    "csp": "ced",
    "ib": "syllabus",
    "book": "book",
    "course": "course",
}

# Version of the node-list JSON contract emitted by to_nodes (see json-format.md).
# Semantic versioning: bump major for any breaking change to an existing field or
# guarantee; minor for backward-compatible additions (e.g. a new field).
# 1.1.0 added the (nullable) "title" field and the "kind" field.
FORMAT_VERSION = "1.1.0"


def parse_front_matter(md):
    """Split optional leading front matter from the markdown body.

    If the text begins with a `---` line, every line up to the next `---` line is
    front matter: simple `key: value` scalar entries (a small YAML subset — no
    nesting or lists; surrounding quotes on the value are stripped). Returns
    (meta dict, body). With no well-formed front matter, returns ({}, md).
    """
    lines = md.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, md
    meta = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "".join(lines[i + 1:])
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"').strip("'")
    # No closing delimiter: not front matter after all — leave the text intact.
    return {}, md


def parse_top_heading(rest):
    """Parse a level-1 heading, returning (flavor, id, head).

    The id is verbatim (e.g. "1" for "Unit 1: ...", "A" for "Theme A: ...", or
    the parenthesized code for a Big Idea); head is the heading's prose.

    Note: a `# Unit N:` heading is reported as "csa"; the course flavor shares it
    and is told apart by heading depth (see detect_flavor).
    """
    m = BIG_IDEA.match(rest)
    if m:
        return "csp", m.group(2), m.group(1)
    m = UNIT.match(rest)
    if m:
        return "csa", m.group(1), m.group(2)
    m = THEME.match(rest)
    if m:
        return "ib", m.group(1), m.group(2)
    m = CHAPTER.match(rest)
    if m:
        return "book", m.group(1), m.group(2)
    sys.exit(f"unparseable top-level heading: {rest!r}")


def detect_flavor(md):
    """Determine the hierarchy flavor by examining all heading levels.

    csp/ib/book are identified by their level-1 heading alone. csa and course
    share the `# Unit N:` heading, so they are told apart by heading depth: csa
    nests headings to level 3+ (topic -> learning objective -> …), while course
    stops at level-2 lessons and lists its objectives as bullets. A `# Unit` file
    with no level-3 heading is therefore course (this also covers a partial course
    whose lessons have no objectives yet).
    """
    _, body = parse_front_matter(md)
    base = None
    max_depth = 0
    for line in body.splitlines():
        m = HEADING.match(line)
        if not m:
            continue
        depth = len(m.group(1))
        max_depth = max(max_depth, depth)
        if depth == 1:
            heading_flavor = parse_top_heading(m.group(2))[0]
            if base is None:
                base = heading_flavor
            elif heading_flavor != base:
                sys.exit(f"mixed hierarchy flavors: {m.group(2)!r}")
    if base is None:
        sys.exit("no top-level heading found")
    return "course" if base == "csa" and max_depth < 3 else base


def parse_sections(md):
    """Walk markdown lines; return (flavor, flat list of section dicts).

    Each section dict has: level, id (verbatim, or synthesized for course
    objectives), head (heading text after the id) and body (raw lines up to the
    next heading or, for course, the next objective bullet).

    The course flavor (see detect_flavor) is handled specially: its level-3 raw
    objectives are top-level markdown bullets, each becoming a level-3 section
    with a synthesized "lessonid.N" id, rather than `###` headings.
    """
    flavor = detect_flavor(md)
    _, body = parse_front_matter(md)
    sections = []
    current = None
    lesson_id = None   # id of the lesson whose bullet objectives we're numbering
    obj_n = 0          # sequential counter for synthesized course objective ids
    for line in body.splitlines():
        m = HEADING.match(line)
        if m:
            if current is not None:
                sections.append(current)
                current = None
            level = len(m.group(1))
            rest = m.group(2)
            if level == 1:
                _, id_, head = parse_top_heading(rest)
                lesson_id, obj_n = None, 0
            else:
                parts = rest.split(" ", 1)
                id_ = parts[0]
                head = parts[1] if len(parts) > 1 else ""
                if flavor == "course" and level == 2:
                    lesson_id, obj_n = id_, 0
            current = {"level": level, "id": id_, "head": head, "body": []}
        elif flavor == "course" and lesson_id is not None and OBJECTIVE_BULLET.match(line):
            # A top-level bullet under a lesson is a level-3 raw objective with a
            # synthesized id (lesson id + sequential number, like IB content).
            if current is not None:
                sections.append(current)
            obj_n += 1
            current = {
                "level": 3,
                "id": f"{lesson_id}.{obj_n}",
                "head": OBJECTIVE_BULLET.match(line).group(1),
                "body": [],
            }
        elif current is not None:
            current["body"].append(line)
    if current is not None:
        sections.append(current)
    return flavor, sections


def section_text(sec):
    """The node's text exactly as it appears in the markdown.

    Joins a section's heading text (after the id) and its body lines
    (paragraphs, lists, code blocks), trimming surrounding blank lines. This is
    the canonical text representation shared by every consumer of the format.
    """
    lines = ([sec["head"]] if sec["head"] else []) + sec["body"]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def to_nodes(md, title=None):
    """Parse a hierarchy markdown file into a flat, already-tagged node list.

    `title` overrides any `title:` in the markdown's front matter; if neither is
    given the emitted title is null.

    Returns a JSON-serializable dict {"version": ..., "flavor": ..., "title": ...,
    "kind": ..., "levels": [...], "nodes": [...]}:

        version - the FORMAT_VERSION of this contract (semver string)
        flavor  - the detected flavor ("csa"/"csp"/"ib"/"book"/"course")
        title   - a human title for the hierarchy, or None if unknown (from the
                  `title` argument, else the front matter's `title:`)
        kind    - the document kind (e.g. "ced", "syllabus", "book"); the front
                  matter's `kind:` if given, else FLAVOR_KIND[flavor]
        levels  - the flavor's level tags in order (levels[i] is the tag for
                  level i+1); the full set the flavor defines, independent of
                  which levels happen to have nodes
        nodes   - the flattened hierarchy; each node is a plain dict:

        id       - the verbatim hierarchy id ("1", "1.1", "1.1.A.1", "CRD-1.A")
        level    - the 1-based heading depth (int)
        tag      - the level's tag for this flavor (LEVEL_TAGS[flavor][level]),
                   e.g. "unit", "topic", "learning-objective"
        parent   - the id of the enclosing node, or None for a level-1 root
        ordinal  - 1-based position among siblings (nodes sharing a parent),
                   in document order
        is_leaf  - True if no node has this node as its parent
        text     - the node's text (see section_text)

    parent/ordinal/is_leaf are derived from the markdown's heading nesting, so a
    consumer can load this with zero flavor knowledge and zero markdown parsing.
    This is the producer side of the cross-repo contract documented in
    HIERARCHIES.md and plans/extract-extractors.md.
    """
    meta, _ = parse_front_matter(md)
    flavor, sections = parse_sections(md)
    tags = LEVEL_TAGS[flavor]
    nodes = []
    # Stack of (level, id) for the currently-open ancestors; a node's parent is
    # the nearest open ancestor at a shallower level (same nesting rule the XML
    # builder uses).
    stack = []
    ordinals = {}        # next sibling ordinal, keyed by parent id (None for roots)
    parents = set()      # ids that are some node's parent -> not leaves
    for sec in sections:
        level = sec["level"]
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if parent is not None:
            parents.add(parent)
        ordinals[parent] = ordinals.get(parent, 0) + 1
        nodes.append({
            "id": sec["id"],
            "level": level,
            "tag": tags[level],
            "parent": parent,
            "ordinal": ordinals[parent],
            "is_leaf": True,  # corrected below once all parents are known
            "text": section_text(sec),
        })
        stack.append((level, sec["id"]))
    for node in nodes:
        if node["id"] in parents:
            node["is_leaf"] = False
    levels = [tags[level] for level in sorted(tags)]
    return {
        "version": FORMAT_VERSION,
        "flavor": flavor,
        "title": title if title is not None else meta.get("title"),
        "kind": meta.get("kind") or FLAVOR_KIND[flavor],
        "levels": levels,
        "nodes": nodes,
    }
