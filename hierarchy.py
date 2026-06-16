"""Parse a curriculum/book hierarchy markdown file into a flat list of sections.

Shared by build_hierarchy_xml.py and build_hierarchy_db.py. The flavor is
detected from the first level-1 heading; sections carry their ids verbatim (e.g.
"1", "1.1", "1.1.A", "1.1.A.1") and consumers apply their own id transformations.

Flavors and their per-level tags:

- csp:  big-idea / essential-understanding / learning-objective / essential-knowledge
- csa:  unit / topic / learning-objective / essential-knowledge
- ib:   theme / topic / subtopic / learning-statement / content
- book: chapter / section / subsection
"""

import re
import sys

HEADING = re.compile(r"^(#{1,5}) (.+)$")
BIG_IDEA = re.compile(r"^Big Idea \d+: (.+) \((\w+)\)$")
UNIT = re.compile(r"^Unit (\d+): (.+)$")
THEME = re.compile(r"^Theme ([AB]): (.+)$")
CHAPTER = re.compile(r"^Chapter (\d+): (.+)$")

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
}


def parse_top_heading(rest):
    """Parse a level-1 heading, returning (flavor, id, head).

    The id is verbatim (e.g. "1" for "Unit 1: ...", "A" for "Theme A: ...", or
    the parenthesized code for a Big Idea); head is the heading's prose.
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


def parse_sections(md):
    """Walk markdown lines; return (flavor, flat list of section dicts).

    Each section dict has: level, id (verbatim), head (heading text after the
    id) and body (raw lines up to the next heading).
    """
    flavor = None
    sections = []
    current = None
    for line in md.splitlines():
        m = HEADING.match(line)
        if m:
            if current is not None:
                sections.append(current)
            level = len(m.group(1))
            rest = m.group(2)
            if level == 1:
                heading_flavor, id_, head = parse_top_heading(rest)
                if flavor is None:
                    flavor = heading_flavor
                elif flavor != heading_flavor:
                    sys.exit(f"mixed hierarchy flavors: {rest!r}")
            else:
                if flavor is None:
                    sys.exit(f"sub-heading before any top-level heading: {rest!r}")
                parts = rest.split(" ", 1)
                id_ = parts[0]
                head = parts[1] if len(parts) > 1 else ""
            current = {"level": level, "id": id_, "head": head, "body": []}
        elif current is not None:
            current["body"].append(line)
    if current is not None:
        sections.append(current)
    if flavor is None:
        sys.exit("no top-level heading found")
    return flavor, sections
