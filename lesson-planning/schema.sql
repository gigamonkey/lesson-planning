-- Lesson-planning database schema (canonical reference).
--
-- The live store for *planning data*: deduped raw objectives, their CED/syllabus
-- coverage, the synthesized lesson objectives, and the ordered lessons. The
-- official outline (the `nodes` table) is file-sourced and treated as read-only
-- reference -- regenerated from the *-hierarchy.md files via load_nodes.py.
--
-- Loaders (load_nodes.py, import_objectives.py) embed the DDL for the tables
-- they own (with IF NOT EXISTS) so they are independently runnable; this file is
-- the authored, in-sync description of the whole schema and can be applied to a
-- fresh database to create every table up front.

-- Official outline nodes, normalized across all flavors (CSA/CSP/IB). Derived
-- from a *-hierarchy.md file by load_nodes.py: one row per node, with its parent,
-- level tag, whether it is a leaf (the unit of "coverage"), and document order.
CREATE TABLE IF NOT EXISTS nodes (
  course    TEXT    NOT NULL,        -- 'csa' | 'csp' | 'ib'
  node_id   TEXT    NOT NULL,        -- verbatim id: '1.1.A.1', 'CRD-1.A', 'A1.1.1.1'
  parent_id TEXT,                    -- parent node_id; NULL for level-1 nodes
  level     TEXT    NOT NULL,        -- level tag: 'unit'|'topic'|'learning-objective'|...
  is_leaf   INTEGER NOT NULL,        -- 1 if the node has no children
  ordinal   INTEGER NOT NULL,        -- document order, for stable display
  text      TEXT    NOT NULL,
  PRIMARY KEY (course, node_id)
);

-- RAW objectives: the atoms the teacher drafted. Deduped; mapped to the CED.
-- Course-agnostic text; a raw objective can belong to >1 course.
CREATE TABLE IF NOT EXISTS objectives (
  uuid   TEXT PRIMARY KEY,
  text   TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'  -- reserved hook (soft-delete/archive); always 'active' today
);

CREATE TABLE IF NOT EXISTS course_objectives (
  course      TEXT NOT NULL,
  uuid        TEXT NOT NULL REFERENCES objectives(uuid),
  position    INTEGER,                       -- per-course raw-objective order in the pool
  -- Plan placement, deepest level wins (CED-style): a raw sits in a lesson
  -- (plan_lesson), or just a unit (plan_unit, rough), or nowhere (both NULL).
  plan_unit   TEXT REFERENCES units(uuid),
  plan_lesson TEXT REFERENCES lessons(uuid),
  PRIMARY KEY (course, uuid)
);

-- Many-to-many: a raw objective covers >=1 official node; a node may be covered
-- by >=1 raw objective.
CREATE TABLE IF NOT EXISTS coverage (
  course  TEXT NOT NULL,
  uuid    TEXT NOT NULL REFERENCES objectives(uuid),
  node_id TEXT NOT NULL,
  PRIMARY KEY (course, uuid, node_id),
  FOREIGN KEY (course, node_id) REFERENCES nodes(course, node_id)
);

-- The teacher's own top-level grouping (distinct from CED units): lessons are
-- organized into units. UUID-identified so titles can change freely.
CREATE TABLE IF NOT EXISTS units (
  uuid     TEXT PRIMARY KEY,
  course   TEXT NOT NULL,
  title    TEXT NOT NULL,
  position INTEGER NOT NULL          -- order within the course
);

-- Lessons live in a unit (unit_id NULL = unassigned). Each lesson has a short
-- title AND one learning_objective (the whiteboard statement, 1:1 with the
-- lesson) authored from the raw objectives placed in it. UUID-identified.
CREATE TABLE IF NOT EXISTS lessons (
  uuid               TEXT PRIMARY KEY,
  course             TEXT NOT NULL,
  unit_id            TEXT REFERENCES units(uuid),  -- NULL = unassigned
  title              TEXT NOT NULL DEFAULT '',
  learning_objective TEXT NOT NULL DEFAULT '',
  position           INTEGER NOT NULL              -- order within the unit
);
