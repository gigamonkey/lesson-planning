#!/usr/bin/env python3
"""Self-contained checks for import_objectives.import_level (turn a reference
hierarchy level into interned, placed objectives). No test framework: run directly.

    uv run test_import_objectives.py

Builds a throwaway db from examples/widgets and asserts: each node at the chosen
level becomes an interned objective placed onto its own node; the placement sticks
on a NON-LEAF level and survives a write_course/read_course round-trip; and a
re-import is idempotent.
"""

import os
import sqlite3
import sys
import tempfile

import import_objectives
import plan_io

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGETS = os.path.join(HERE, "examples", "widgets")
COURSE = "widgets"
REF = "ced"
# In examples/widgets/ced.md the levels are
#   unit, topic, learning-objective, essential-knowledge
# and a learning-objective node (e.g. "1.1.A") has essential-knowledge children,
# so it is genuinely NON-LEAF -- the case the feature exists for.
LEVEL = "learning-objective"


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    plan_io.read_course(path, WIDGETS)
    return path


def _level_nodes(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT node_id, text, is_leaf FROM nodes WHERE course=? AND hierarchy=?"
            " AND level=? ORDER BY ordinal", (COURSE, REF, LEVEL)).fetchall()
    finally:
        conn.close()


def _coverage(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {(r[0], r[1]) for r in conn.execute(  # (uuid, node_id)
            "SELECT uuid, node_id FROM coverage WHERE course=? AND hierarchy=?",
            (COURSE, REF))}
    finally:
        conn.close()


def _objective_uuid(db_path, text):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT uuid FROM objectives WHERE course=? AND text=?",
                           (COURSE, text)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _pool_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT count(*) FROM course_objectives WHERE course=?",
                           (COURSE,)).fetchone()[0]
    finally:
        conn.close()


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def main():
    db_path = _fresh_db()
    try:
        nodes = _level_nodes(db_path)
        check(len(nodes) >= 2, f"the {LEVEL!r} level has nodes ({len(nodes)})")
        check(any(not n[2] for n in nodes),
              "at least one target node is NON-LEAF (the case under test)")
        pool_before = _pool_count(db_path)

        # --- import the level ---
        stats = import_objectives.import_level(db_path, COURSE, REF, LEVEL)
        check(stats["placed"] == len(nodes),
              f"placed one objective per node ({stats['placed']} == {len(nodes)})")

        cov = _coverage(db_path)
        for node_id, text, _is_leaf in nodes:
            uuid = _objective_uuid(db_path, text)
            check(uuid is not None, f"node {node_id} text interned as an objective")
            check((uuid, node_id) in cov,
                  f"objective for {node_id} is placed onto its own node")
        check(_pool_count(db_path) == pool_before + len(nodes),
              "every new objective was added to the pool")

        # --- non-leaf placement survives a write/read round-trip ---
        with tempfile.TemporaryDirectory() as d:
            plan_io.write_course(db_path, COURSE, d)
            rt_db = _fresh_db_from_dir(d)
            try:
                rt_cov = _coverage(rt_db)
                for node_id, text, is_leaf in nodes:
                    if is_leaf:
                        continue
                    uuid = _objective_uuid(rt_db, text)
                    check((uuid, node_id) in rt_cov,
                          f"NON-LEAF placement on {node_id} survived round-trip")
            finally:
                os.remove(rt_db)

        # --- idempotency: a second import mints nothing new ---
        cov_before = _coverage(db_path)
        pool_mid = _pool_count(db_path)
        stats2 = import_objectives.import_level(db_path, COURSE, REF, LEVEL)
        check(stats2["objectives_new"] == 0, "re-import minted 0 new objectives")
        check(_pool_count(db_path) == pool_mid, "re-import added 0 pool rows")
        check(_coverage(db_path) == cov_before, "re-import added 0 coverage edges")

        print("\nAll import_objectives.import_level checks passed.")
    finally:
        os.remove(db_path)


def _fresh_db_from_dir(course_dir):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    plan_io.read_course(path, course_dir)
    return path


if __name__ == "__main__":
    main()
