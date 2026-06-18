-- Lesson-planning database schema (canonical reference).
--
-- Everything is a HIERARCHY of nodes, and objectives are the connective tissue
-- between nodes across hierarchies. Reference hierarchies (CED/IB/book) are
-- file-sourced and read-only -- regenerated from the *-hierarchy.md files via
-- load_nodes.py. Outline hierarchies (a course's lesson plan; later a planned
-- book) are authored in-app. An objective's reference "coverage" and its lesson
-- "placement" are the SAME relation: a coverage edge to a node in some hierarchy.
--
-- Loaders (load_nodes.py, import_objectives.py) embed the DDL for the tables
-- they own (with IF NOT EXISTS) so they are independently runnable; this file is
-- the authored, in-sync description of the whole schema and can be applied to a
-- fresh database to create every table up front.

-- Courses are the top-level organizing principle: a short human id (also the
-- /<course> URL) and a title. Every hierarchy belongs to a course.
CREATE TABLE IF NOT EXISTS courses (
  course TEXT PRIMARY KEY,            -- 'csa', 'csp', 'ib'
  title  TEXT NOT NULL               -- 'AP Computer Science A'
);

-- Registry of every hierarchy (a tree of nodes): the CED/IB/book references and
-- authored outlines like a course lesson plan. The slug is an opaque-but-readable
-- handle (never parsed -- the course/kind/editable columns carry the meaning).
CREATE TABLE IF NOT EXISTS hierarchies (
  hierarchy TEXT PRIMARY KEY,         -- readable handle: 'csa-ced', 'csa-plan' (opaque)
  course    TEXT NOT NULL REFERENCES courses(course),
  kind      TEXT NOT NULL,            -- WHAT it is: 'ced'|'ib-syllabus'|'lesson-plan'|'book'
  editable  INTEGER NOT NULL,         -- 0 = reference (external, read-only) | 1 = authored
  title     TEXT NOT NULL,
  source    TEXT                      -- reference: the markdown path; authored: NULL
);

-- Nodes of any hierarchy (one row per node), keyed by hierarchy (not course).
-- Reference rows are regenerated from markdown (node_id = verbatim id, read-only);
-- outline rows are authored in-app (node_id = uuid; parent_id/ordinal mutable).
CREATE TABLE IF NOT EXISTS nodes (
  hierarchy TEXT    NOT NULL REFERENCES hierarchies(hierarchy),
  node_id   TEXT    NOT NULL,        -- verbatim id ('1.1.A.1', 'CRD-1.A') or a uuid
  parent_id TEXT,                    -- parent node_id; NULL for top-level nodes
  level     TEXT    NOT NULL,        -- per-hierarchy vocab: 'topic'|'ek'… or 'unit'|'lesson'
  is_leaf   INTEGER NOT NULL,        -- 1 if the node has no children (the unit of coverage)
  ordinal   INTEGER NOT NULL,        -- order within its sibling group, for stable display
  text      TEXT    NOT NULL,        -- statement / title
  PRIMARY KEY (hierarchy, node_id)
);

-- RAW objectives: the atoms the teacher drafted. Course-agnostic text; a raw
-- objective can belong to >1 course. Text is the natural key (UNIQUE): importers
-- and the app intern by text -- find-or-create -- so identical text never yields
-- two objectives (they share one uuid and accumulate coverage edges).
CREATE TABLE IF NOT EXISTS objectives (
  uuid   TEXT PRIMARY KEY,
  text   TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active'  -- reserved hook (soft-delete/archive); always 'active' today
);

-- The raw-objective pool, course-scoped (membership + pool order). Plan placement
-- is NOT here -- it is a coverage edge into the course's outline hierarchy.
CREATE TABLE IF NOT EXISTS course_objectives (
  course   TEXT NOT NULL,
  uuid     TEXT NOT NULL REFERENCES objectives(uuid),
  position INTEGER,                  -- per-course raw-objective order in the pool
  PRIMARY KEY (course, uuid)
);

-- Objective <-> node, in ANY hierarchy. Reference edges = "covers this standard";
-- outline edges = "placed at this lesson/unit". The schema allows many edges per
-- (hierarchy, uuid); the app enforces single placement per outline (relaxable).
CREATE TABLE IF NOT EXISTS coverage (
  hierarchy TEXT NOT NULL,
  uuid      TEXT NOT NULL REFERENCES objectives(uuid),
  node_id   TEXT NOT NULL,
  PRIMARY KEY (hierarchy, uuid, node_id),
  FOREIGN KEY (hierarchy, node_id) REFERENCES nodes(hierarchy, node_id)
);

-- Generic per-node extras for authored outlines (sparse, stringly-typed). E.g.
-- ('csa-plan', <lesson-uuid>, 'learning_objective', 'Declare and use variables').
CREATE TABLE IF NOT EXISTS node_attr (
  hierarchy TEXT NOT NULL,
  node_id   TEXT NOT NULL,
  name      TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (hierarchy, node_id, name),
  FOREIGN KEY (hierarchy, node_id) REFERENCES nodes(hierarchy, node_id)
);

-- Pair an outline with the reference(s) it is measured against, so the UI can
-- show the outline's coverage stats (gaps/planned) against the reference.
CREATE TABLE IF NOT EXISTS hierarchy_targets (
  outline   TEXT NOT NULL REFERENCES hierarchies(hierarchy),
  reference TEXT NOT NULL REFERENCES hierarchies(hierarchy),
  PRIMARY KEY (outline, reference)
);
