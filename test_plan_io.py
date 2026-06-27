#!/usr/bin/env python3
"""Self-contained checks for plan_io's plan.md round-trip and load_plan_text (the
in-memory loader behind the web Markdown editor). No test framework: run directly.

    uv run test_plan_io.py

Builds a throwaway db from examples/widgets and asserts the invariants the editor
relies on: load is a fixpoint, reword-with-token preserves identity (and coverage),
a tokenless bullet mints a new objective, and reference coverage survives an
outline-only edit.
"""

import os
import re
import sqlite3
import sys
import tempfile

import plan_io

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGETS = os.path.join(HERE, "examples", "widgets")


def _render(db_path, course="widgets"):
    conn = sqlite3.connect(db_path)
    try:
        files, _n_obj, _n_cov = plan_io.render_course(conn, course)
        return files[plan_io.PLAN_FILE]
    finally:
        conn.close()


def _count(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


# Two objectives.tsv rows with identical text (two uuids) -- the state a clean git
# merge of two branches that each created the same objective produces.
_A = "aaaa1111-2222-3333-4444-555555555555"
_B = "bbbb1111-2222-3333-4444-555555555555"

_DUP_PLAN = """\
---
course: dup
title: Dup Test
primary_outline: plan
targets: ref
---

# Unit: U

## Lesson A

- Explain recursion.  (#aaaa)

## Lesson B

- Explain recursion.  (#bbbb)
"""

_DUP_REF = """\
---
slug: ref
levels: unit, item
title: Ref
---

# Unit 1: Things

## n1 First item

## n2 Second item
"""

_DUP_OBJECTIVES = f"uuid\ttext\n{_A}\tExplain recursion.\n{_B}\tExplain recursion.\n"
_DUP_COVERAGE = f"uuid\thierarchy_id\tnode_id\n{_A}\tref\tn1\n{_B}\tref\tn2\n"


def _write_dup_course(course_dir):
    os.makedirs(course_dir, exist_ok=True)
    for name, text in (("plan.md", _DUP_PLAN), ("ref.md", _DUP_REF),
                       ("objectives.tsv", _DUP_OBJECTIVES), ("coverage.tsv", _DUP_COVERAGE)):
        with open(os.path.join(course_dir, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)


def check_merge_unifies_same_text():
    """Two same-text objectives (distinct uuids) -- from merging two branches -- are
    unified onto one uuid, with both placements and both reference edges rewritten
    to the winner; the load leaves no foreign-key violations."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "dup.db")
        _write_dup_course(os.path.join(tmp, "dup"))
        plan_io.read_course(db, os.path.join(tmp, "dup"))

        # Exactly one objective for the text, and it's the winner (smallest uuid).
        assert _count(db, "SELECT count(*) FROM objectives WHERE course='dup'") == 1, \
            "same-text duplicates were not unified to one objective"
        win = _count(db, "SELECT uuid FROM objectives WHERE course='dup'")
        assert win == _A, f"winner should be the smallest uuid {_A!r}, got {win!r}"
        assert _count(db, "SELECT count(*) FROM objectives WHERE uuid=?", (_B,)) == 0, \
            "the loser uuid should not survive"
        # The pool holds one objective (deduped).
        assert _count(db, "SELECT count(*) FROM course_objectives WHERE course='dup'") == 1, \
            "pool should hold one membership row after unification"
        # Both placements survive on the winner (placement conflict kept, not lost).
        assert _count(db, "SELECT count(*) FROM coverage WHERE course='dup'"
                      " AND hierarchy='plan' AND uuid=?", (win,)) == 2, \
            "both outline placements should be rewritten onto the winner"
        # Both reference edges unified onto the winner (n1 from A, n2 from B).
        refs = _count(db, "SELECT count(*) FROM coverage WHERE course='dup'"
                      " AND hierarchy='ref' AND uuid=?", (win,))
        assert refs == 2, f"both reference edges should unify onto the winner, got {refs}"
        # FK enforcement leaves the loaded db internally consistent.
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == [], \
                "loaded db has foreign-key violations"
        finally:
            conn.close()
    print("ok - merge unifies same-text objectives")


def main():
    check_merge_unifies_same_text()
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        plan_io.read_course(db, WIDGETS)
        text = _render(db)

        # 1. Loading the canonical render back is a fixpoint.
        plan_io.load_plan_text(db, "widgets", text)
        assert _render(db) == text, "load_plan_text is not a fixpoint"

        ref_cov = "SELECT count(*) FROM coverage WHERE hierarchy<>'widgets-plan'"
        ref_before = _count(db, ref_cov)
        pool_before = _count(db, "SELECT count(*) FROM course_objectives WHERE course='widgets'")

        # Reword a tokened bullet (identity must survive) and add a tokenless one.
        lines = text.splitlines()
        token = None
        for i, line in enumerate(lines):
            m = re.search(r"\(#([0-9a-fA-F]+)\)\s*$", line)
            if line.startswith("- ") and m:
                token = m.group(1)
                lines[i] = f"- A REWORDED OBJECTIVE  (#{token})"
                break
        assert token, "no tokened bullet found in the example"
        uuid_for_token = _count(
            db, "SELECT uuid FROM course_objectives co JOIN objectives o USING(uuid)"
            " WHERE co.course='widgets' AND o.uuid LIKE ?", (token + "%",))
        lines.append("- A brand-new tokenless objective")
        plan_io.load_plan_text(db, "widgets", "\n".join(lines) + "\n")

        # 2. Pool grew by exactly one (the new bullet); reword did not add a row.
        assert _count(db, "SELECT count(*) FROM course_objectives WHERE course='widgets'") \
            == pool_before + 1, "pool count wrong after reword + new bullet"
        # 3. Reword adopted the new text on the SAME uuid (identity preserved).
        assert _count(db, "SELECT count(*) FROM objectives WHERE uuid=? AND text=?",
                      (uuid_for_token, "A REWORDED OBJECTIVE")) == 1, \
            "reword did not preserve objective identity"
        # 4. The tokenless bullet became a fresh objective.
        assert _count(db, "SELECT count(*) FROM objectives WHERE text=?",
                      ("A brand-new tokenless objective",)) == 1, \
            "tokenless bullet was not interned as a new objective"
        # 5. Reference coverage (not represented in plan.md) is untouched.
        assert _count(db, ref_cov) == ref_before, "reference coverage was clobbered"

        # 6. Front-matter course mismatch is rejected without mutating anything.
        before = _render(db)
        try:
            plan_io.load_plan_text(db, "widgets", "---\ncourse: other\n---\n# Unit: X\n")
        except ValueError:
            pass
        else:
            raise AssertionError("course mismatch was not rejected")
        assert _render(db) == before, "rejected load still mutated the db"

        # 7. Unit/lesson durations round-trip through the editor loader and reach
        #    node_duration. (A redundant "(1 day)" lesson tag is the default, so it
        #    is dropped -- not asserted here.)
        base = _render(db)
        lines2, added = base.splitlines(), 0
        for i, l in enumerate(lines2):
            if l.startswith("# Unit:"):
                lines2[i] = l + " (2 weeks)"
                added += 1
                break
        for i, l in enumerate(lines2):
            if l.startswith("## "):   # every H2 is now a lesson (pool is H1)
                lines2[i] = l + " (3 days)"
                added += 1
                break
        src2 = "\n".join(lines2) + "\n"
        plan_io.load_plan_text(db, "widgets", src2)
        assert _render(db) == src2, "durations did not round-trip through the editor loader"
        assert _count(db, "SELECT count(*) FROM node_duration") == added, \
            "duration rows not stored as expected"
        assert added >= 1, "test setup: no unit/lesson heading found to tag"

    print("ok - all plan_io / load_plan_text checks passed")


if __name__ == "__main__":
    sys.exit(main())
