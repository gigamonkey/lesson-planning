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
  uuid        TEXT PRIMARY KEY,
  text        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'merged' | 'draft'
  merged_into TEXT REFERENCES objectives(uuid) -- set when status='merged' (dedup)
);

CREATE TABLE IF NOT EXISTS course_objectives (
  course TEXT NOT NULL,
  uuid   TEXT NOT NULL REFERENCES objectives(uuid),
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

-- The teacher's own structure: an ordered list of lessons per course.
CREATE TABLE IF NOT EXISTS lessons (
  id       INTEGER PRIMARY KEY,
  course   TEXT NOT NULL,
  title    TEXT NOT NULL,
  position INTEGER NOT NULL          -- order within the course
);

-- LESSON objectives: the student-facing statement (whiteboard objective),
-- synthesized from >=1 raw objective. End state: one per lesson. lesson_id is
-- nullable while a lesson objective is still being drafted/unscheduled.
CREATE TABLE IF NOT EXISTS lesson_objectives (
  id        INTEGER PRIMARY KEY,
  course    TEXT NOT NULL,
  text      TEXT NOT NULL,
  lesson_id INTEGER REFERENCES lessons(id),
  position  INTEGER                  -- order within the lesson (NULL = unscheduled)
);

-- Roll-up: which raw objectives a lesson objective encompasses (many-to-many).
CREATE TABLE IF NOT EXISTS objective_rollup (
  lesson_objective_id INTEGER NOT NULL REFERENCES lesson_objectives(id),
  objective_uuid      TEXT    NOT NULL REFERENCES objectives(uuid),
  PRIMARY KEY (lesson_objective_id, objective_uuid)
);
