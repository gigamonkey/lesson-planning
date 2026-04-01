# Sumarize CED

Read `ced-2025.pdf` which contains the official Course and Exam Description
(CED) for the latest revision of the AP CSA curriculum.

Your goal is to extract the required course material which are organized into a
hierarchy of units, topics, learning objectives, and essential knowledge items.

Units are number 1-4 and have titles like “Using Objects and Methods”.

Topics have two-part identifiers made up of two numbers separated with a `.`.
Like “1.1 - Introduction to Algorithms, Programming, and Compilers”.

Learning objectives, have three part identifiers, the first two parts coming
from the topic and the third being a letter. For example “1.1.A Represent
patterns algorithms found in everyday life using written language or diagrams.”

And essential knowledge items have four-part identifiers, the first three parts
of which come from the learning objective they are part of and the fourth part
being a number. For example, "1.1.A.1 - Algorithms define step-by-step processes
to follow when completing a task or solving a problem. These algorithms can be
represented using written language or diagrams."

All three of those can be found on page 30 of `ced-2025.pdf`.

Please extract *all* the unit titles, and the topics, learning objectives, and
essential knowledge items into a Markdown file `ced-2025-hierarchy.md`. Capture
just the identifiers and text from the CED for the topics, learning objectives,
and essential knowledge items but capture the exact text from the CED.

Arrange them into a hierarchy of header levels with `#` for units, `##` for
topics, `###` for learning objects, and `####` for essential knowledge items.
