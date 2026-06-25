-- Lesson-planning database schema (canonical reference).
--
-- Everything is a HIERARCHY of nodes, and objectives are the connective tissue
-- between nodes across hierarchies. Reference hierarchies (CED/IB/book) are
-- file-sourced and read-only -- regenerated from the *.md files via load_nodes.py.
-- Outline hierarchies (a course's lesson plan; later a planned book) are authored
-- in-app. An objective's reference "coverage" and its lesson "placement" are the
-- SAME relation: a coverage edge to a node in some hierarchy.
--
-- IDENTITY: a hierarchy is identified by (course, hierarchy) where `hierarchy` is
-- the course-relative ("bare") slug -- e.g. ('csa','ced'), ('csa','plan'). The
-- slug is unique only WITHIN its course, so every table that references a
-- hierarchy carries `course` alongside `hierarchy` and keys on the pair. On disk
-- the slug is stored bare (pinned in front matter, the filename, coverage.tsv,
-- targets); the course scope comes from the course directory. See
-- plans/hierarchy-identity-and-kind.md.
--
-- The db.db file is a disposable cache, rebuilt from the markdown corpus
-- (rebuild_db.py applies this file then loads every course). It is never migrated
-- in place; change the schema here and rebuild.

-- Courses are the top-level organizing principle: a short human id (also the
-- /<course> URL) and a title. Every hierarchy belongs to a course.
CREATE TABLE IF NOT EXISTS courses (
  course TEXT PRIMARY KEY,            -- 'csa', 'csp', 'ib-sl'
  title  TEXT NOT NULL,              -- 'AP Computer Science A'
  -- The course's official outline (the editable lesson-plan hierarchy), by its
  -- bare slug. The SOLE outline identifier (not inferred from kind/editable).
  primary_outline   TEXT,
  -- Calendar binding for the calendar view: a bells calendar id (a JSON file in
  -- LESSON_CALENDAR_DIR, e.g. 'bhs-2025-2026'). The school year's span comes from
  -- that calendar (firstDay..lastDay); courses run the full year for now.
  calendar    TEXT,
  FOREIGN KEY (course, primary_outline) REFERENCES hierarchies(course, hierarchy)
);

-- Registry of every hierarchy (a tree of nodes): the CED/IB/book references and
-- authored outlines like a course lesson plan. `hierarchy` is the bare,
-- course-relative slug (e.g. 'ced', 'book', 'plan'); identity is (course,
-- hierarchy). `kind` is optional free-form provenance (College Board CED, a
-- textbook, BJC, ...) -- a display label only; nothing branches on it.
CREATE TABLE IF NOT EXISTS hierarchies (
  course    TEXT NOT NULL REFERENCES courses(course),
  hierarchy TEXT NOT NULL,             -- bare slug: 'ced', 'book', 'plan'
  kind      TEXT,                      -- provenance label (optional, free-form)
  editable  INTEGER NOT NULL,          -- 0 = reference (external, read-only) | 1 = authored
  title     TEXT NOT NULL,
  source    TEXT,                      -- reference: the markdown filename; authored: NULL
  source_md TEXT,                      -- reference: the verbatim source markdown,
                                       --   replayed by write_course so the corpus
                                       --   stays self-contained; authored: NULL
  PRIMARY KEY (course, hierarchy)
);

-- Nodes of any hierarchy (one row per node), keyed by (course, hierarchy).
-- Reference rows are regenerated from markdown (node_id = verbatim id, read-only);
-- outline rows are authored in-app (node_id = uuid; parent_id/ordinal mutable).
CREATE TABLE IF NOT EXISTS nodes (
  course    TEXT    NOT NULL,
  hierarchy TEXT    NOT NULL,         -- bare slug
  node_id   TEXT    NOT NULL,        -- verbatim id ('1.1.A.1', 'CRD-1.A') or a uuid
  parent_id TEXT,                    -- parent node_id; NULL for top-level nodes
  level     TEXT    NOT NULL,        -- per-hierarchy vocab: 'topic'|'ek'… or 'unit'|'lesson'
  is_leaf   INTEGER NOT NULL,        -- 1 if the node has no children (the unit of coverage)
  ordinal   INTEGER NOT NULL,        -- order within its sibling group, for stable display
  text      TEXT    NOT NULL,        -- statement / title
  PRIMARY KEY (course, hierarchy, node_id),
  FOREIGN KEY (course, hierarchy) REFERENCES hierarchies(course, hierarchy)
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
-- (course, hierarchy, uuid); the app enforces single placement per outline.
CREATE TABLE IF NOT EXISTS coverage (
  course    TEXT NOT NULL,
  hierarchy TEXT NOT NULL,             -- bare slug
  uuid      TEXT NOT NULL REFERENCES objectives(uuid),
  node_id   TEXT NOT NULL,
  position  INTEGER,                  -- order of this objective WITHIN (course, hierarchy, node_id);
                                      -- independent of course_objectives.position (the master
                                      -- per-course pool order). NULL sorts last (append).
  PRIMARY KEY (course, hierarchy, uuid, node_id),
  FOREIGN KEY (course, hierarchy, node_id) REFERENCES nodes(course, hierarchy, node_id)
);

-- Generic per-node extras for authored outlines (sparse, stringly-typed). E.g.
-- ('csa', 'plan', <lesson-uuid>, 'learning_objective', 'Declare and use variables').
CREATE TABLE IF NOT EXISTS node_attr (
  course    TEXT NOT NULL,
  hierarchy TEXT NOT NULL,
  node_id   TEXT NOT NULL,
  name      TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (course, hierarchy, node_id, name),
  FOREIGN KEY (course, hierarchy, node_id) REFERENCES nodes(course, hierarchy, node_id)
);

-- How long a node is meant to take. Authored in markdown as a trailing heading
-- tag, e.g. '# Unit: Selection (2 weeks)', '## Hello, world (3 days)', or (on a
-- reference) '## A1 Computer fundamentals (18 hours)'. The calendar view lays the
-- OUTLINE out using unit weeks + lesson days; reference durations (hours) are
-- stored for reporting but don't drive the calendar. One duration per node.
CREATE TABLE IF NOT EXISTS node_duration (
  course    TEXT NOT NULL,
  hierarchy TEXT NOT NULL,
  node_id   TEXT NOT NULL,
  amount    REAL NOT NULL,            -- 2, 0.5, 18
  unit      TEXT NOT NULL,            -- 'week' | 'day' | 'hour' (stored singular)
  PRIMARY KEY (course, hierarchy, node_id),
  FOREIGN KEY (course, hierarchy, node_id) REFERENCES nodes(course, hierarchy, node_id)
);

-- Pair an outline with the reference(s) it is measured against, so the UI can
-- show the outline's coverage stats (gaps/planned) against the reference. Both
-- sides are hierarchies of the same course (by bare slug). Every reference is a
-- target, so this doubles as the course's ordered list of references: `position`
-- is the display order (the order they were added; the plan.md `targets:` list).
CREATE TABLE IF NOT EXISTS hierarchy_targets (
  course    TEXT NOT NULL,
  outline   TEXT NOT NULL,             -- bare slug
  reference TEXT NOT NULL,             -- bare slug
  position  INTEGER,                   -- order among the course's references
  PRIMARY KEY (course, outline, reference),
  FOREIGN KEY (course, outline)   REFERENCES hierarchies(course, hierarchy),
  FOREIGN KEY (course, reference) REFERENCES hierarchies(course, hierarchy)
);
