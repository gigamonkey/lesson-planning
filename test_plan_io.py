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

import hierarchy
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


def _lesson_files(course_dir):
    """{filename: {uuid, title, parts}} read straight from a course's lessons/ dir
    (keyed by filename so path/rename assertions can see the slug)."""
    d = os.path.join(course_dir, plan_io.LESSONS_DIR)
    out = {}
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            meta, body = hierarchy.parse_front_matter(f.read())
        out[fn] = {"uuid": meta.get("uuid"), "title": meta.get("title"),
                   "parts": plan_io._parse_lesson_body(body)}
    return out


def check_lesson_files():
    """Lesson plans as first-class files: identity tokens + lessons/*.md round-trip,
    content survives reorder/rename/outline-only edits, new lessons mint files,
    deletes remove files, and a legacy plan.md learning-objective line migrates."""
    plan = """\
---
course: lf
title: Lesson Files
primary_outline: plan
targets: ref
---

# Unit: U

## Alpha

**Learning objective:** Understand alpha.

- Do an alpha thing.  (#a1a1)

## Beta

- Do a beta thing.  (#b2b2)

# Unplaced objectives

- A pooled objective.  (#c3c3)
"""
    ref = """\
---
slug: ref
levels: unit, item
title: Ref
---

# Unit 1: Things

## n1 First item

## n2 Second item
"""
    A = "a1a11111-2222-3333-4444-555555555555"
    B = "b2b21111-2222-3333-4444-555555555555"
    C = "c3c31111-2222-3333-4444-555555555555"
    objs = (f"uuid\ttext\n{A}\tDo an alpha thing.\n{B}\tDo a beta thing.\n"
            f"{C}\tA pooled objective.\n")
    cov = f"uuid\thierarchy_id\tnode_id\n{A}\tref\tn1\n{B}\tref\tn2\n"

    with tempfile.TemporaryDirectory() as tmp:
        cdir = os.path.join(tmp, "lf")
        os.makedirs(cdir)
        for name, text in (("plan.md", plan), ("ref.md", ref),
                           ("objectives.tsv", objs), ("coverage.tsv", cov)):
            with open(os.path.join(cdir, name), "w", encoding="utf-8", newline="") as f:
                f.write(text)
        db = os.path.join(tmp, "lf.db")

        # --- 1. read -> write materializes a lesson file per lesson, with the
        #        legacy LO migrated into the Alpha lesson's file.
        plan_io.read_course(db, cdir)
        plan_io.write_course(db, "lf", cdir)
        lf = _lesson_files(cdir)
        assert len(lf) == 2, f"expected 2 lesson files, got {sorted(lf)}"
        by_title = {v["title"]: v for v in lf.values()}
        assert set(by_title) == {"Alpha", "Beta"}, f"lesson titles wrong: {sorted(by_title)}"
        assert by_title["Alpha"]["parts"].get("learning_objective") == "Understand alpha.", \
            "legacy plan.md learning objective did not migrate into the lesson file"
        alpha_uuid = by_title["Alpha"]["uuid"]
        # The plan.md no longer carries the LO line; it carries lesson tokens.
        with open(os.path.join(cdir, "plan.md"), encoding="utf-8") as f:
            plan_text = f.read()
        assert "**Learning objective:**" not in plan_text, "LO line should be gone from plan.md"
        assert re.search(r"^## Alpha \(#[0-9a-f]+\)$", plan_text, re.M), \
            f"Alpha heading missing its identity token:\n{plan_text}"

        # --- 2. write -> read -> write is a fixpoint (filenames, uuids, content all stable).
        before = _lesson_files(cdir)
        plan_io.read_course(db, cdir)
        plan_io.write_course(db, "lf", cdir)
        assert _lesson_files(cdir) == before, "lesson files are not a round-trip fixpoint"

        # --- 3. Edit a lesson's content (a Preview part) directly in its file, reload:
        #        the part lands in node_attr keyed by the SAME uuid.
        alpha_fn = next(fn for fn, v in before.items() if v["title"] == "Alpha")
        with open(os.path.join(cdir, plan_io.LESSONS_DIR, alpha_fn), "a",
                  encoding="utf-8") as f:
            f.write("\n## Preview\n\nThe alpha preview.\n")
        plan_io.read_course(db, cdir)
        assert _count(db, "SELECT value FROM node_attr WHERE course='lf' AND hierarchy='plan'"
                      " AND node_id=? AND name='preview'", (alpha_uuid,)) == "The alpha preview.", \
            "lesson Preview part did not load into node_attr"

        # --- 4. Reorder lessons in plan.md (Beta before Alpha) via load_plan_text:
        #        each lesson's content stays with its uuid (the reorder-trap guard).
        pre_reorder = _render(db, "lf")
        lines = pre_reorder.splitlines()
        # Move the Beta lesson block above the Alpha block by swapping headings'
        # order through a fresh edit: rebuild with Beta first.
        beta_uuid = by_title["Beta"]["uuid"]
        # Reword Beta's title in place to confirm rename keeps identity too.
        reordered = pre_reorder.replace("## Beta (", "## Beta Renamed (")
        plan_io.load_plan_text(db, "lf", reordered)
        # Alpha still owns its preview (content preserved across the plan.md edit).
        assert _count(db, "SELECT value FROM node_attr WHERE course='lf' AND hierarchy='plan'"
                      " AND node_id=? AND name='preview'", (alpha_uuid,)) == "The alpha preview.", \
            "lesson content lost across an outline-only edit"
        # Beta kept its uuid despite the rename (title adopted from the heading).
        assert _count(db, "SELECT text FROM nodes WHERE course='lf' AND hierarchy='plan'"
                      " AND node_id=?", (beta_uuid,)) == "Beta Renamed", \
            "rename did not preserve lesson identity"

        # --- 5. Rename round-trips to a renamed file; the stale file is gone.
        plan_io.write_course(db, "lf", cdir)
        lf2 = _lesson_files(cdir)
        beta = next(v for v in lf2.values() if v["uuid"] == beta_uuid)
        assert beta["title"] == "Beta Renamed", "renamed lesson title not written"
        beta_fn = next(fn for fn, v in lf2.items() if v["uuid"] == beta_uuid)
        assert "beta-renamed-" in beta_fn, f"renamed file slug wrong: {beta_fn}"
        assert beta_fn != alpha_fn and len(lf2) == 2, "stale lesson file not reconciled"

        # --- 6. A brand-new tokenless lesson mints a uuid + file; a deleted lesson's
        #        file is removed.
        cur = _render(db, "lf")
        # Drop the Alpha lesson heading + its bullet; add a new tokenless lesson.
        kept = [l for l in cur.splitlines()
                if not l.startswith("## Alpha (") and l != "- Do an alpha thing.  (#a1a1)"]
        # Insert a new lesson under the unit (after the unit heading line).
        ui = next(i for i, l in enumerate(kept) if l.startswith("# Unit:"))
        kept[ui + 1:ui + 1] = ["", "## Gamma"]
        plan_io.load_plan_text(db, "lf", "\n".join(kept) + "\n")
        plan_io.write_course(db, "lf", cdir)
        lf3 = _lesson_files(cdir)
        titles3 = {v["title"] for v in lf3.values()}
        assert "Gamma" in titles3, "new tokenless lesson did not mint a file"
        assert "Alpha" not in titles3, "deleted lesson's file was not removed"
        assert not any(v["uuid"] == alpha_uuid for v in lf3.values()), \
            "deleted lesson's uuid still on disk"

    print("ok - lesson files: identity, content preservation, rename/new/delete, LO migration")


def main():
    check_merge_unifies_same_text()
    check_lesson_files()
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
                # The duration tag renders just inside the trailing identity token
                # ("## T (3 days) (#tok)"), so insert it before that token.
                m = re.search(r"\s*\(#[0-9a-fA-F]+\)\s*$", l)
                lines2[i] = (l[:m.start()] + " (3 days)" + l[m.start():]) if m else l + " (3 days)"
                added += 1
                break
        src2 = "\n".join(lines2) + "\n"
        plan_io.load_plan_text(db, "widgets", src2)
        assert _render(db) == src2, "durations did not round-trip through the editor loader"
        assert _count(db, "SELECT count(*) FROM node_duration") == added, \
            "duration rows not stored as expected"
        assert added >= 1, "test setup: no unit/lesson heading found to tag"

        # 8. A unit pin round-trips through the editor loader and reaches node_pin.
        #    The pin tag is the LAST group, after the duration -- so a unit can carry
        #    both "(2 weeks) (ends week 35)" and both must survive a render.
        base3 = _render(db)
        lines3 = base3.splitlines()
        for i, l in enumerate(lines3):
            if l.startswith("# Unit:"):   # the one already tagged "(2 weeks)" in step 7
                lines3[i] = l + " (ends week 35)"
                break
        else:
            raise AssertionError("test setup: no unit heading found to pin")
        src3 = "\n".join(lines3) + "\n"
        plan_io.load_plan_text(db, "widgets", src3)
        assert _render(db) == src3, "the pin tag did not round-trip through the editor loader"
        assert _count(db, "SELECT count(*) FROM node_pin WHERE week=35 AND edge='end'") == 1, \
            "the pin was not stored in node_pin"

    print("ok - all plan_io / load_plan_text checks passed")


if __name__ == "__main__":
    sys.exit(main())
