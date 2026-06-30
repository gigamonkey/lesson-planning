"""Parse a curriculum/book hierarchy markdown file into a flat list of sections.

The curriculum-hierarchy markdown parser this repo owns (see FORMAT.md); used by
load_nodes.py (references) and plan_io.py (helpers). There is no "flavor" concept:
heading *depth* encodes tree depth, ids are verbatim (the level-1 id via a small
list of id-extraction patterns, deeper ids the first whitespace token), and level
*names* are declared in the required `levels:` front-matter key (an ordered list,
depth 1 first). So a producer states its own vocabulary rather than the parser
inferring it. See plans/retire-flavor-sniffing.md for the history.

The known level-1 heading shapes parse_root_id recognizes (then a generic
`# ID TEXT` fallback, id = first token, for any new format):

- `# Big Idea N: TITLE (CODE)` -> id is the parenthesized CODE
- `# Unit N: TITLE`            -> id "N"
- `# Theme X: TITLE`           -> id "X"
- `# Chapter N: TITLE`         -> id "N"

The course OUTLINE (units/lessons + objective bullets) is a separate concern,
parsed by plan_io.parse_plan, not here.
"""

import re
import sys

HEADING = re.compile(r"^(#{1,5}) (.+)$")
BIG_IDEA = re.compile(r"^Big Idea \d+: (.+) \((\w+)\)$")
UNIT = re.compile(r"^Unit (\d+): (.+)$")
THEME = re.compile(r"^Theme ([AB]): (.+)$")
CHAPTER = re.compile(r"^Chapter (\d+): (.+)$")
# Fallback level-1 heading: `# ID TEXT` (id = first whitespace token), the same
# shape every deeper heading uses, so a new format needs no bespoke pattern.
GENERIC_ROOT = re.compile(r"^(\S+)\s+(.+)$")
# A raw objective bullet in the course outline: a top-level (column-0) markdown
# bullet. Used by plan_io.parse_plan (the outline parser), not by to_nodes.
OBJECTIVE_BULLET = re.compile(r"^[-*] +(.+)$")

# A trailing duration tag on a node's heading, e.g. "… (2 weeks)", "… (3 days)",
# "… (18 hours)". Strict and only the LAST parenthesized group, so an incidental
# "(HL only)" in a title is never mistaken for it. Amount may be a decimal.
DURATION_RE = re.compile(r"\s*\((\d+(?:\.\d+)?)\s+(weeks?|days?|hours?)\)\s*$")
_DURATION_UNIT = {"week": "week", "weeks": "week", "day": "day", "days": "day",
                  "hour": "hour", "hours": "hour"}


def split_duration(head):
    """Split a trailing duration tag off a heading line.

    Returns (clean_head, duration) where duration is {"amount": float, "unit":
    "week"|"day"|"hour"} or None. The unit is normalized to singular; pluralization
    happens on output (format_duration)."""
    m = DURATION_RE.search(head or "")
    if not m:
        return head, None
    return head[:m.start()].rstrip(), {
        "amount": float(m.group(1)), "unit": _DURATION_UNIT[m.group(2)]}


def format_duration(duration):
    """Inverse of split_duration: " (N unit[s])" for a duration dict, or "" for
    None. An integral amount prints without a decimal point ("2 weeks", not
    "2.0 weeks")."""
    if not duration:
        return ""
    amount = duration["amount"]
    amount = int(amount) if float(amount).is_integer() else amount
    unit = duration["unit"] + ("" if amount == 1 else "s")
    return f" ({amount} {unit})"


# A trailing pin tag on an outline UNIT heading, e.g. "… (starts week 1)",
# "… (ends week 35)": it anchors the unit's start/end on a calendar school-week
# number instead of flowing it sequentially (see calendar_view + FORMAT.md). It is
# the LAST parenthesized group on the line (after any duration tag), and the
# keyword ("starts"/"ends") keeps it distinct from a "(N weeks)" duration.
PIN_RE = re.compile(r"\s*\((starts|ends)\s+week\s+(\d+)\)\s*$")


def split_pin(head):
    """Split a trailing pin tag off a heading line.

    Returns (clean_head, pin) where pin is {"edge": "start"|"end", "week": int} or
    None. Only units pin; strip the pin BEFORE the duration (pin is the last group)."""
    m = PIN_RE.search(head or "")
    if not m:
        return head, None
    return head[:m.start()].rstrip(), {
        "edge": "start" if m.group(1) == "starts" else "end", "week": int(m.group(2))}


def format_pin(pin):
    """Inverse of split_pin: " (starts week N)" / " (ends week N)", or "" for None."""
    if not pin:
        return ""
    return f" ({'starts' if pin['edge'] == 'start' else 'ends'} week {pin['week']})"


# Version of the node-list document emitted by to_nodes (see FORMAT.md).
# Semantic versioning: bump major for any breaking change to an existing field or
# guarantee; minor for backward-compatible additions (e.g. a new field).
# 1.1.0 added the (nullable) "title" field and the "kind" field.
# 1.2.0 added the (nullable) per-node "duration" field.
# 1.3.0 sources "levels" (and each node's tag) from a now-required `levels:`
#       front-matter key instead of the detected flavor.
# 2.0.0 makes the format metadata-driven and removes the "flavor" and "kind"
#       concepts. The output drops "flavor"/"kind" and adds "slug" (the front
#       matter's bare id, optional). The required `levels:` front matter carries
#       level names; `title:` required. Level-1 ids come from a small pattern list
#       + a generic `# ID TEXT` fallback (no flavor). Breaking: `levels:` required.
FORMAT_VERSION = "2.0.0"


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


def parse_root_id(rest):
    """Parse a level-1 heading, returning (id, head).

    The id is verbatim. The known curriculum heading shapes are tried first
    (`# Unit N:` -> id "N", `# Theme A:` -> "A", `# Chapter N:` -> "N",
    `# Big Idea N: … (CODE)` -> the parenthesized CODE), then a generic
    `# ID TEXT` (id = first whitespace token, like every deeper heading level).
    These are pure id-extraction heuristics -- no flavor is inferred."""
    m = BIG_IDEA.match(rest)
    if m:
        return m.group(2), m.group(1)     # id is the parenthesized code
    for pat in (UNIT, THEME, CHAPTER):
        m = pat.match(rest)
        if m:
            return m.group(1), m.group(2)
    m = GENERIC_ROOT.match(rest)
    if m:
        return m.group(1), m.group(2)
    sys.exit(f"unparseable top-level heading: {rest!r}")


def parse_sections(md):
    """Walk markdown lines; return a flat list of section dicts.

    Each section dict has: level (heading depth), id (verbatim -- the level-1 id
    via parse_root_id, deeper ids the first whitespace token of the heading),
    head (heading text after the id) and body (raw lines up to the next heading).
    """
    _, body = parse_front_matter(md)
    sections = []
    current = None
    for line in body.splitlines():
        m = HEADING.match(line)
        if m:
            if current is not None:
                sections.append(current)
                current = None
            level = len(m.group(1))
            rest = m.group(2)
            if level == 1:
                id_, head = parse_root_id(rest)
            else:
                parts = rest.split(" ", 1)
                id_ = parts[0]
                head = parts[1] if len(parts) > 1 else ""
            current = {"level": level, "id": id_, "head": head, "body": []}
        elif current is not None:
            current["body"].append(line)
    if current is not None:
        sections.append(current)
    return sections


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


def parse_levels(meta):
    """The required `levels:` front-matter value as an ordered list of level tags
    (`levels[i]` names heading depth i+1). The value is a comma-separated list,
    e.g. `levels: unit, lab, page`. Raises SystemExit if absent or empty.

    Level *names* live in the markdown (the producer knows them) rather than being
    inferred from the heading shape."""
    names = [s.strip() for s in (meta.get("levels") or "").split(",") if s.strip()]
    if not names:
        sys.exit("reference hierarchy markdown requires a 'levels:' front-matter "
                 "key: a comma-separated list of level names in depth order, "
                 "e.g. 'levels: unit, lab, page'")
    return names


def to_nodes(md, title=None):
    """Parse a hierarchy markdown file into a flat, already-tagged node list.

    `title` overrides any `title:` in the markdown's front matter; if neither is
    given the emitted title is null.

    Returns a JSON-serializable dict {"version": ..., "slug": ..., "title": ...,
    "levels": [...], "nodes": [...]}:

        version - the FORMAT_VERSION of this contract (semver string)
        slug    - the front matter's `slug:` (the bare, course-relative id), or
                  None. Present in the STORED form; absent in the SOURCE
                  form an extractor emits (the app assigns/pins it on upload). When
                  None the consumer falls back to the filename stem. See FORMAT.md.
        title   - a human title for the hierarchy, or None if unknown (from the
                  `title` argument, else the front matter's `title:`)
        levels  - the declared level tags in order (levels[i] tags heading depth
                  i+1), from the required `levels:` front matter
        nodes   - the flattened hierarchy; each node is a plain dict:

        id       - the verbatim hierarchy id ("1", "1.1", "1.1.A.1", "CRD-1.A")
        level    - the 1-based heading depth (int)
        tag      - the level's declared tag (levels[level-1]), e.g. "unit",
                   "topic", "learning-objective"
        parent   - the id of the enclosing node, or None for a level-1 root
        ordinal  - 1-based position among siblings (nodes sharing a parent),
                   in document order
        is_leaf  - True if no node has this node as its parent
        text     - the node's text (see section_text), with any trailing duration
                   tag stripped off
        duration - the heading's duration tag as {"amount": float, "unit":
                   "week"|"day"|"hour"}, or None

    parent/ordinal/is_leaf are derived from the markdown's heading nesting, so a
    consumer can load this with zero markdown parsing.
    """
    meta, _ = parse_front_matter(md)
    sections = parse_sections(md)
    level_names = parse_levels(meta)
    title = title if title is not None else meta.get("title")
    if not title:
        sys.exit("reference hierarchy markdown requires a 'title:' front-matter key")
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
        # A trailing duration tag rides the heading; strip it off the head before
        # building the node text so the stored title is clean.
        sec["head"], duration = split_duration(sec["head"])
        if level > len(level_names):
            sys.exit(f"node {sec['id']!r} is at heading depth {level}, but the "
                     f"'levels:' front matter only names {len(level_names)} "
                     f"level(s): {', '.join(level_names)}")
        nodes.append({
            "id": sec["id"],
            "level": level,
            "tag": level_names[level - 1],
            "parent": parent,
            "ordinal": ordinals[parent],
            "is_leaf": True,  # corrected below once all parents are known
            "text": section_text(sec),
            "duration": duration,
        })
        stack.append((level, sec["id"]))
    for node in nodes:
        if node["id"] in parents:
            node["is_leaf"] = False
    return {
        "version": FORMAT_VERSION,
        "slug": meta.get("slug"),
        "title": title,
        "levels": level_names,
        "nodes": nodes,
    }
