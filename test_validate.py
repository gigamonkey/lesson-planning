#!/usr/bin/env python3
"""Self-contained checks for validate.validate_course. No test framework: run directly.

    uv run test_validate.py

Copies examples/widgets to a throwaway dir, asserts it's clean, then injects each
flavor of corruption (a node id where a UUID belongs; a dangling objective /
lesson / coverage reference; a bad coverage node_id / hierarchy_id) and asserts
the matching problem is reported -- and that nothing else trips.
"""

import os
import re
import shutil
import sys
import tempfile

import validate

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGETS = os.path.join(HERE, "examples", "widgets")

_failures = 0


def check(label, ok):
    global _failures
    if ok:
        print(f"ok - {label}")
    else:
        _failures += 1
        print(f"FAIL - {label}")


def _fresh():
    d = tempfile.mkdtemp(prefix="validate-")
    dst = os.path.join(d, "widgets")
    shutil.copytree(WIDGETS, dst)
    return dst


def _patch(path, old, new):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert old in text, f"{old!r} not found in {path}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))


def _has(problems, *needles):
    """True if some problem contains all the needles (case-insensitive)."""
    return any(all(n.lower() in prob.lower() for n in needles) for prob in problems)


def main():
    # Baseline: the example course is clean.
    clean = _fresh()
    check("clean widgets course has no problems", validate.validate_course(clean) == [])

    # objectives.tsv: a node id where a UUID belongs.
    d = _fresh()
    _patch(os.path.join(d, "objectives.tsv"),
           "221ab336-50ac-41c8-9d97-799a3d9051b5", "1.1.A.2")
    probs = validate.validate_course(d)
    check("objectives.tsv non-uuid flagged", _has(probs, "objectives.tsv", "not a UUID"))

    # coverage.tsv: uuid that isn't a UUID.
    d = _fresh()
    _patch(os.path.join(d, "coverage.tsv"),
           "faf31c3e-78f6-4bab-819f-e45b0693890f", "1.1.A.1")
    probs = validate.validate_course(d)
    check("coverage.tsv non-uuid flagged", _has(probs, "coverage.tsv", "not a UUID"))

    # coverage.tsv: a well-formed UUID that names no objective.
    d = _fresh()
    _patch(os.path.join(d, "coverage.tsv"),
           "faf31c3e-78f6-4bab-819f-e45b0693890f",
           "00000000-0000-4000-8000-000000000000")
    probs = validate.validate_course(d)
    check("coverage.tsv dangling objective flagged",
          _has(probs, "coverage.tsv", "no objective"))

    # coverage.tsv: a node id that doesn't exist in the reference hierarchy.
    d = _fresh()
    _patch(os.path.join(d, "coverage.tsv"), "ced\t1.1.A.1", "ced\t9.9.Z.9")
    probs = validate.validate_course(d)
    check("coverage.tsv unknown node_id flagged",
          _has(probs, "coverage.tsv", "9.9.Z.9", "not in hierarchy"))

    # coverage.tsv: an unknown hierarchy slug.
    d = _fresh()
    _patch(os.path.join(d, "coverage.tsv"), "ced\t1.1.A.1", "bogus\t1.1.A.1")
    probs = validate.validate_course(d)
    check("coverage.tsv unknown hierarchy flagged",
          _has(probs, "coverage.tsv", "bogus", "not a reference hierarchy"))

    # A lesson file with a node id for its uuid.
    d = _fresh()
    _patch(os.path.join(d, "lessons", "what-is-a-widget-3aae8d7b.md"),
           "uuid: 3aae8d7b", "uuid: 1.2")
    probs = validate.validate_course(d)
    check("lesson-file non-uuid flagged", _has(probs, "lessons/", "not a UUID"))

    # plan.md: an objective bullet token that resolves to nothing.
    d = _fresh()
    _patch(os.path.join(d, "plan.md"), "(#faf3)", "(#ffffffff)")
    probs = validate.validate_course(d)
    check("plan.md dangling objective token flagged",
          _has(probs, "plan.md objective", "matches no objective"))

    # plan.md: a lesson heading token that resolves to no lesson file.
    d = _fresh()
    _patch(os.path.join(d, "plan.md"), "(#3aae)", "(#ffffffff)")
    probs = validate.validate_course(d)
    check("plan.md dangling lesson token flagged",
          _has(probs, "plan.md lesson", "matches no lesson file"))

    if _failures:
        print(f"\n{_failures} check(s) failed")
        sys.exit(1)
    print("\nok - all validate checks passed")


if __name__ == "__main__":
    main()
