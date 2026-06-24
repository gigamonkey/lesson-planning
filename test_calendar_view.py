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

    # L1 (3 days) is one block spanning three columns; the two remaining days of
    # week 1 are one free box each (unfilled days render per day).
    first = weekrows[0]["cells"]
    assert first[0] == {"title": "L1", "days": 3, "kind": "lesson"}, first
    assert [c["kind"] for c in first] == ["lesson", "free", "free"], first
    assert all(c["days"] == 1 for c in first[1:]), first

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

    # A full-week break (all of Sep 8-12 off) between two 1-week units becomes its
    # own section; mid-unit it stays inline.
    data2 = dict(DATA, lastDay="2025-09-26",
                 holidays=["2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-12"],
                 breakNames={"2025-09-08": "Fall Break"})
    bs2 = bells.BellSchedule([data2], {"role": "student"})

    between = cv.build_calendar(bs2, data2, [
        {"title": "A", "weeks": 1, "lessons": []},
        {"title": "B", "weeks": 1, "lessons": []}])
    kinds = ["break" if u.get("break_section") else "unit" for u in between["units"]]
    assert kinds == ["unit", "break", "unit"], kinds
    sec = between["units"][1]
    assert sec["rows"][0]["kind"] == "break" and sec["rows"][0]["name"] == "Fall Break", sec

    inside = cv.build_calendar(bs2, data2, [{"title": "A", "weeks": 2, "lessons": []}])
    assert [u.get("break_section", False) for u in inside["units"]] == [False], inside
    assert any(r["kind"] == "break" for r in inside["units"][0]["rows"]), \
        "a break mid-unit should stay inline in the unit's rows"

    print("ok - all calendar_view checks passed")


if __name__ == "__main__":
    sys.exit(main())
