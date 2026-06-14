# Summarize CSA CED

In the csa directory read `ced-2025.pdf` which contains the official Course and
Exam Description (CED) for the latest (2025) revision of the AP CSA curriculum.

Your goal is to extract the required course material which is organized into a
four-level hierarchy of units, topics, learning objectives, and essential
knowledge items.

There are four Units numbered from 1 to 4 with titles like “Using Objects and
Methods” and “Selection and Iteration”. Unlike the CSP Big Ideas, units have no
short code—they are identified just by their number.

Topics have two-part identifiers made up of the unit number followed by a "."
and a number. Like “1.1 Introduction to Algorithms, Programming, and
Compilers”.

Learning objectives have three-part identifiers, the first two parts coming
from the topic and the third being a capital letter. For example “1.1.A
Represent patterns and algorithms found in everyday life using written language
or diagrams.”

And Essential Knowledge items have four-part identifiers, the first three parts
of which come from the learning objective they are part of and the fourth part
being a number. For example, “1.1.A.1 Algorithms define step-by-step processes
to follow when completing a task or solving a problem.”

All three of those can be found on the page labeled “Course Framework V.1 | 30”
of the CED pdf (the 36th page of the PDF file).

Please extract *all* the unit titles, and the topics, learning objectives, and
essential knowledge items into a Markdown file `csa/ced-2025-hierarchy.md`.
Capture just the identifiers and text from the CED for the Units, Topics,
Learning Objectives, and Essential Knowledge items but capture the exact text
from the CED. Please preserve the formatting of the text as much as possible,
such as bulleted lists and code blocks.

Arrange them into a hierarchy of header levels with `#` for Units, `##` for
topics, `###` for learning objectives, and `####` for essential knowledge
items. Format the unit headings as `# Unit N: TITLE` and the other headings as
the identifier followed by the text, like `## 1.1 Introduction to Algorithms,
Programming, and Compilers`.

This is a big task since you need to extract all the elements at every level of
the hierarchy so you will probably need to farm this out to multiple agents.
