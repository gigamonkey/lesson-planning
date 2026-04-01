# Compare books

I would like you to make a plan for doing a comprehensive review of the contents
of CSAwesome and BHSawesome, both textbooks for teaching AP Computer Science A.
BHSawesome is a revision of CSAwesome.

You can use the current state of both books as well as the git history to see
how BHSawesome was developed from CSAwesome.

In the `repos` directory are clones of the `CSAwesome2` and `BHSawesome2`
projects which contain the source of the two books. You should be able to see
from the git history that BHSawesome2 has common ancestry with CSAwemsome2 but
that they have since diverged.

The goal of BHSawesome was to reorder the material in CSAwesome and to simplify
the presentation. But it is definitely possible that there are parts of
CSAwesome that do not have an equivalent in BHSawesome and I'd like your
analysis to identify them.

I'm particularly interested in any topics that are covered in CSAwesome that are
not covered at all in BHSawesome and seem to be related to items from the
College Board Course and Exam Description which you can most easily access in
the file `ced/ced-2025-hierarchy.md` which gives an outline of the whole course.

You may also want to use some of the scripts we've developed here, especially
`list_files.py` which you can use like this to see the files from each book in
the order they appear in the book's text when it is rendered for the web:

```
uv run list_files.py repos/BHSawesome2/pretext/main.ptx
uv run list_files.py repos/CSAwesome2/pretext/main.ptx
```

Just the source of the two books has been extracted into the directories
`csawesome` and `bhsawesome` using teh `just-pretext.sh` script.

This will be a large analysis so you may need to plan how to farm out the work
to multiple agents though I'm not sure about that.

---

## Plan

### Overview

The 2025 AP CSA Course and Exam Description has **4 units** (not 5 — inheritance
was removed from the curriculum). The CED topics are 1.1–1.15, 2.1–2.12,
3.1–3.9, and 4.1–4.17. CSAwesome still carries a Unit 5 (Inheritance) from the
prior curriculum, but this is no longer exam-relevant.

BHSawesome reorganizes CSAwesome's content into ~14 topical chapters, reordering
material to introduce topics differently and simplify presentation. The analysis
needs to identify what **current CED-relevant** content exists in CSAwesome but
is missing from BHSawesome.

### Structural comparison

**CSAwesome** follows the College Board unit structure directly:
- Unit 0: Getting Started (preface, IDEs, growth mindset, pretests, survey)
- Unit 1: Using Objects and Methods (topics 1.1–1.15, summaries, practice, GUIs, FRQs)
- Unit 2: Selection and Iteration (topics 2.1–2.12, summaries, practice, FRQs, Magpie lab, Consumer Review lab)
- Unit 3: Class Creation (topics 3.1–3.9, summary, practice, FRQs, CB Labs, community challenge)
- Unit 4: Data Collections (topics 4.1–4.17, arrays/ArrayLists/2D arrays, search/sort, recursion, hashmap, Picture lab)
- Unit 5: Inheritance (topics 5.1–5.7) — **legacy content, not in 2025 CED**
- Supplemental: Tests, TimedTests, MixedFreeResponse, FreeResponse, Stories/Interviewees

**BHSawesome** reorganizes into topical chapters:
- frontmatter, introduction
- primitive-types-and-variables (CED 1.2–1.5)
- methods (CED 1.9, 1.7, 1.11)
- booleans-and-conditionals (CED 2.1, 2.3, 2.2, 2.6)
- loops (CED 2.7–2.9, 2.11, 2.12)
- arrays (CED 4.3–4.5, 4.11–4.13)
- strings (CED 1.15, 2.10)
- classes (CED 3.3–3.5)
- objects (CED 3.6, 3.8, plus turtles)
- array-lists (CED 4.7–4.10)
- text-files (CED 4.6, 4.2)
- algorithms (CED 4.14–4.17)
- abstraction-and-program-design (CED 3.1, 1.8, 3.2)
- ap-practice, free-response

### Phase 1: CED topic coverage gap analysis

Map every 2025 CED topic (1.1–1.15, 2.1–2.12, 3.1–3.9, 4.1–4.17) to files in
both books. Identify CED topics that have a dedicated file in CSAwesome but no
corresponding file in BHSawesome.

Note: CSAwesome's Unit 5 (Inheritance, topics 5.1–5.7) is from the **prior**
curriculum and is not part of the 2025 CED. BHSawesome's omission of this
material is correct and not a gap.

**Likely missing or reduced CED topics in BHSawesome** (from file-level analysis):

| CED Topic | CSAwesome file | BHSawesome status |
|-----------|---------------|-------------------|
| 1.6 Compound Assignment Operators | `topic-1-6-compound-operators.ptx` | No dedicated file — may be folded into another topic or missing |
| 1.10 Calling Class Methods | `topic-1-10-calling-class-methods.ptx` | No dedicated file |
| 1.12 Objects: Instances of Classes | `topic-1-12-objects.ptx` | No dedicated file |
| 1.13 Object Creation (Instantiation) | `topic-1-13-constructors.ptx` | No dedicated file (BHS has `topic-3-4-constructors.ptx` in classes chapter, but that covers CED 3.4, not 1.13) |
| 1.14 Calling Instance Methods | `topic-1-14-calling-instance-methods.ptx` | No dedicated file |
| 2.4 Nested if Statements | `topic-2-4-nested-ifs.ptx` | No dedicated file |
| 2.5 Compound Boolean Expressions | `topic-2-5-compound-ifs.ptx` | No dedicated file |
| 3.7 Class Variables and Methods | `topic-3-7-static-vars-methods.ptx` | No dedicated file |
| 3.9 this Keyword | `topic-3-9-this.ptx` | No dedicated file |
| 4.1 Ethical/Social Issues Around Data | `topic-4-1-data-ethics.ptx` | No dedicated file |

This is the file-level view. Some "missing" topics may be covered inline within
other files (e.g., compound assignment operators might appear in the variables
chapter). Phase 2 will verify.

### Phase 2: Content-level verification

For each topic flagged as potentially missing in Phase 1, read the relevant
BHSawesome files to check whether the content is covered inline. This requires
searching BHSawesome source for key terms and concepts from each CED topic.

**Method**: For each potentially missing topic, grep the BHSawesome source for
distinctive terms (e.g., `+=` and `++` for compound operators, `nested` and
`else if` for nested ifs, `static` for class variables, `extends` and
`super` for inheritance, etc.).

Dispatch this as parallel agents — one per CED unit group:
1. **Agent A**: Check CED Unit 1 gaps (1.6, 1.10, 1.12, 1.13, 1.14) in BHSawesome
2. **Agent B**: Check CED Unit 2 gaps (2.4, 2.5) in BHSawesome
3. **Agent C**: Check CED Unit 3 gaps (3.7, 3.9) in BHSawesome
4. **Agent D**: Check CED Unit 4 gap (4.1) in BHSawesome

Each agent should:
- Search BHSawesome source files for key concepts from the CED topic
- Read relevant sections to assess depth of coverage
- Report: fully covered / partially covered / not covered
- Note where content appears (if anywhere)

### Phase 3: Supplemental content comparison

Beyond CED topics, compare supplemental materials:

| Content type | CSAwesome | BHSawesome |
|-------------|-----------|------------|
| Magpie Lab (chatbot) | Unit 2 (5 files) | Not present |
| Consumer Review Lab | Unit 2 | Not present |
| Picture Lab | Unit 4 (9 files) | Not present |
| Community Challenge | Unit 3 | Not present |
| JavaSwing GUIs | Unit 1 | Not present |
| Stories/Interviewees | Stories/ (14 interviewees) | Not present |
| HashMap | Unit 4 | Not present |
| Timed Tests | TimedTests/ (4 tests) | Not present |
| Untimed Tests | Tests/ (5 tests) | Not present |
| Mixed Free Response | MixedFreeResponse/ | Not present |
| Pretests/Posttests/Survey | Unit 0, posttest/ | Not present |

BHSawesome has some content CSAwesome doesn't:
- `text-files/` chapter (topics 4.6, 4.2 exist in both but BHS organizes them as a chapter)
- `turtles.ptx` in objects chapter
- Different free response organization

### Phase 4: Git history analysis

Use `git log` on `repos/BHSawesome2` to understand:
- When files were removed or reorganized vs CSAwesome
- The overall pattern of simplification
- Whether any CED-relevant content was intentionally removed vs accidentally dropped

This can be done with a single agent examining the git history for key
structural changes (file renames, deletions, additions).

### Phase 5: Produce summary report

Compile findings into a report with:

1. **CED coverage matrix**: Every 2025 CED topic (1.1–1.15, 2.1–2.12, 3.1–3.9,
   4.1–4.17) with coverage status in both books (full/partial/none) and file
   locations
2. **Critical gaps**: CED topics missing from BHSawesome that are exam-relevant
3. **Supplemental content differences**: Labs, practice tests, and enrichment
   materials present in CSAwesome but not BHSawesome
4. **Content present in BHSawesome but not CSAwesome** (if any)
5. **Legacy content**: Note CSAwesome's Unit 5 (Inheritance) as retained from
   the prior curriculum but no longer in the 2025 CED

### Execution approach

- Phase 1 is already largely done above from the file listing analysis
- Phase 2 is the most work-intensive — use 4 parallel agents
- Phase 3 can be done quickly from the file listings (mostly done above)
- Phase 4 is a single agent task
- Phase 5 is synthesis — write the final report as `plans/compare-books-report.md`

### Key finding preview

CSAwesome's Unit 5 (Inheritance) is **not a gap** — inheritance was removed from
the 2025 AP CSA curriculum. BHSawesome correctly omits it.

The potentially significant gaps are all within the current 4-unit CED:
- **CED 1.6, 1.10, 1.12–1.14**: Topics about compound operators, calling class
  methods, objects/instantiation, and calling instance methods — these may be
  covered inline in BHSawesome's reorganized chapters but need verification.
- **CED 2.4–2.5**: Nested ifs and compound Boolean expressions — likely folded
  into the booleans-and-conditionals chapter but need to confirm coverage depth.
- **CED 3.7, 3.9**: Static variables/methods and `this` keyword — no dedicated
  files, need to check if covered elsewhere.
- **CED 4.1**: Data ethics — no dedicated file.

Phase 2's content-level search will determine which of these are true gaps vs
content reorganized into other files.
