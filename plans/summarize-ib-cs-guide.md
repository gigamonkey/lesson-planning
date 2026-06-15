# Summarize IB computer science guide

In the ib directory read `ib-cs-guide-2025.pdf` which contains the official
description for latest revision of the IB CS curriculum.

Your goal is to extract the required course material which is organized into a
four-level hierarchy of themes, areas, topics, learning objectives, and essential
knowledge items.

There are two themes, A and B.

Themes are broken down into areas with ids like "A1" and titles like "Computer
fundamentals".

Topics have ids like "A1.1" and titles like "Computer hardware and operation".

Learning objectives have three-part identifiers made up of the area id followed
by a "." and a number. Like “A1.1.1 Decsribe the functions and interactions of
the main CPU components”.

Finally essential knowledge items, under each learning object, don't have
identifiers but you can synthesize one as the learning objective id plus a
number so "A1.1.1.1 Units: arithmetic logic unit (ALU), control unit (CU)".

Please extract *all* the theme, area, topic, learning objectives, and essential
knowledge items into a Markdown file `ib/ib-hierarchy.md`. Capture just the
identifiers and text from the pdf but make sure to capture the exact text. The
content you need starts on page 28 tha has a header "Syllabus content".

Arrange them into a hierarchy of header levels (`#` to `##`, etc.)
items. Format the theme headings as `# Theme X: TITLE` and the other headings as
the identifier followed by the text, like `## A1.1 Computer hardware and operation".

This is a big task since you need to extract all the elements at every level of
the hierarchy so you will probably need to farm this out to multiple agents.
