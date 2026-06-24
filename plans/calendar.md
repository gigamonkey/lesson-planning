# Calendar

In the bhs-cs repo there is code to display a course calendar based on the
course outlines in the `courses/` directory. It fill the calendar using the
Javascript `@peterseibel/bells` library to map units and lessons to the actual
weeks and days of the school year, skipping breaks, etc. The `bells` library we
depend on is the Python version of that library.

Please add a feature to `app.py` to display a similar calendar view based on a
course's outline hierarchy. This will presumably require us to have a first
class way to attach durations to hierarchy nodes. That will mean a syntax in the
Markdown of hierarchies as well as new tables in the database for storing the
associations. The main units of time we care about are how many weeks a unit it
supposed to last and how many days a lesson is supposed to take (usually one but
sometimes more). Note there are already some times noted in the titles of the
IB hierarchies so once we have a mechanism for associating durations with nodes
we can use that to make those times a first class thing.

The calendar view can be rendered purely server side; the client side rendering
in bhs-cs was mostly a remnant of an early version of the site which was purely
static.

Some of the relevant files you may want to start with in understanding the
bhs-cs code are these but there are no doubt others.

- bhs-cs/views/pages/calendar.njk
- bhs-cs/client-js/js/calendar-builder.ts
- bhs-cs/modules/calendar-outline.ts
- bhs-cs/modules/calendar.ts
- bhs-cs/modules/bells-instance.ts

Please ask any questions you have about specifics of what I'm asking for and
then write out a complete plan in this file.
