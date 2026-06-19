# Categorize CSA objectives into BHSawesome hierarchy

Please read the file csa/learning-objectives/csa-objectives.tsv which contains
uuid, objective text pairs. Then read the csa/bhsawesome-outline.md to get the
hierarchy of the BHSawesome text book and the full text of BHSawesome in
bhsawesome/ and produce a csa-objectives-bhsawesome.tsv containing the original
uuid and objective text and a third column containing the ID of the subsection
that the objective seems to fit best into. Pick one subsection per objective; if
two subsections seem equally good prefer the one earlier in the book. Please use
the full text of the book to determine where an objective fits best as the
headers in the outline may not contain enough information.

You'll probably need to farm this out to subagents as the book is pretty long.
You should try to assign every objective to a subsection but don't force it; if
there really is no good match it is better to omit an objective from the output
file as that indicates a true gap in the book.
