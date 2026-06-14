# Sumarize CSP CED

In the csp directory read
`ap-computer-science-principles-course-and-exam-description.pdf` which contains
the official Course and Exam Description (CED) for the latest revision of the AP
CSP curriculum.

Your goal is to extract the required course material which are organized into a
four-level hierarchy of big ideas, enduring understandings, learning objectives,
and essential knowledge items.

There are five Big Ideas numbered from 1 to 5 and have titles like “Creative
Development” and “Data”. They each also have a short code like “CRD” for
“Creative Development” and “DAT” for “Data”.

Enduring understandings have two-part identifiers made up of the short code for
the encompassing Big Idea followed by a "-" and a number. Like “CRD-1
Incorporating multiple perspectives through collaboration improves computing
innovations as they are developed.".

Learning objectives, have three part identifiers, the first two parts coming
from the Enduring understanding and the third being a letter. For example
“CRD-1.A Explain how computing innovations are improved through collaboration."

And Essential Knowledge items have four-part identifiers, the first three parts
of which come from the learning objective they are part of and the fourth part
being a number. For example, “CRD-1.A.1 - A computing innovation includes a
program as an intergral part of its function.“

All three of those can be found on page 32 of of the CED pdf.

Please extract *all* the unit titles, and the topics, learning objectives, and
essential knowledge items into a Markdown file `csp/ced-hierarchy.md`. Capture
just the identifiers and text from the CED for the Big Ideas, Enduring
Understandings, Learning Objectives, and Essential Knowledge items but capture
the exact text from the CED.

Arrange them into a hierarchy of header levels with `#` for Big Ideas, `##` for
Enduring Understandings, `###` for learning objects, and `####` for essential
knowledge items.

This is a big task since you need to extract all the elements at every level of
the hierarchy so you will probably need to farm this out to multiple agents.
