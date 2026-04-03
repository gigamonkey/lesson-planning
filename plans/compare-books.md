# Make a plan to compare CSAwesome and BHSawesome

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
