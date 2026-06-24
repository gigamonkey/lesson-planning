#!/usr/bin/env python3
"""Self-contained checks for calendar_view's layout engine. No test framework.

    uv run test_calendar_view.py

Builds a tiny three-week synthetic school calendar with one Monday holiday and
asserts the "loose weeks" rule (a holiday week still counts as one week), lesson
placement across school days, and the unscheduled-weeks warning.
"""

import sys

import bells
import calendar_view as cv

# Sep 1 2025 is a Monday; Sep 19 is a Friday -> three Mon-Fri weeks. Sep 8 (the
# Monday of week 2) is a holiday, so week 2 has four school days but still counts.
DATA = {
    "year": "2025-2026", "id": "test", "name": "Test School",
    "timezone": "America/Los_Angeles",
    "firstDay": "2025-09-01", "lastDay": "2025-09-19",
    "schedules": {"NORMAL": [{"name": "P1", "start": "8:30", "end": "9:28"}]},
    "holidays": ["2025-09-08"],
    "breakNames": {},
}


def main():
    bs = bells.BellSchedule([DATA], {"role": "student"})

    units = [
        {"title": "U1", "weeks": 2, "lessons": [{"title": "L1", "days": 3}]},
        {"title": "U2", "weeks": None, "lessons": [{"title": "L2", "days": 1}]},
    ]
    view = cv.build_calendar(bs, DATA, units)

    assert view["teaching_weeks"] == 3, view["teaching_weeks"]

    u1 = view["units"][0]
    weekrows = [r for r in u1["rows"] if r["kind"] == "week"]
    assert len(weekrows) == 2, "U1 (2 weeks) should consume two teaching weeks"
    assert weekrows[0]["school_days"] == 5, weekrows[0]
    # The holiday week still counts as one week, with four school days ("loose").
    assert weekrows[1]["school_days"] == 4, weekrows[1]

    # L1 (3 days) lands on the first three school days; the rest of week 1 is free.
    first = weekrows[0]["cells"]
    assert first[0] == {"title": "L1", "days": 3, "kind": "lesson"}, first
    assert first[1]["kind"] == "free" and first[1]["days"] == 2, first

    # U2 has no week count -> derived, consuming one teaching week for its 1 lesson.
    u2 = view["units"][1]
    assert u2["derived"], "U2 should be auto-sized"
    assert sum(1 for r in u2["rows"] if r["kind"] == "week") == 1, u2["rows"]

    # Both units cover 3 teaching weeks total -> none left over, no overflow.
    assert not any("unscheduled" in w for w in view["warnings"]), view["warnings"]
    assert not u1["overflow"] and not u2["overflow"]

    # Overflow: a unit asking for more lesson-days than its weeks hold.
    over = cv.build_calendar(bs, DATA, [
        {"title": "Big", "weeks": 1, "lessons": [{"title": "X", "days": 9}]}])
    assert over["units"][0]["overflow"], "a 9-day lesson in a 5-day week should overflow"

    print("ok - all calendar_view checks passed")


if __name__ == "__main__":
    sys.exit(main())
