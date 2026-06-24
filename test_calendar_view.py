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

    def kind(u):
        return ("break" if u.get("break_section")
                else "unplanned" if u.get("unplanned") else "unit")

    between = cv.build_calendar(bs2, data2, [
        {"title": "A", "weeks": 1, "lessons": []},
        {"title": "B", "weeks": 1, "lessons": []}])
    kinds = [kind(u) for u in between["units"]]
    assert kinds[:3] == ["unit", "break", "unit"], kinds
    sec = between["units"][1]
    assert sec["rows"][0]["kind"] == "break" and sec["rows"][0]["name"] == "Fall Break", sec
    assert sec["rows"][0]["days"] == 9, sec   # Sep 6-14 inclusive
    # The leftover teaching week at the end is filled with an Unplanned chunk.
    assert kinds[-1] == "unplanned" and between["units"][-1]["title"] == "Unplanned", kinds

    inside = cv.build_calendar(bs2, data2, [{"title": "A", "weeks": 2, "lessons": []}])
    assert not inside["units"][0]["break_section"] and not inside["units"][0]["unplanned"]
    assert any(r["kind"] == "break" for r in inside["units"][0]["rows"]), \
        "a break mid-unit should stay inline in the unit's rows"

    # A two-week break is one box reporting its length in days (Sep 6-21 = 16).
    data3 = dict(DATA, lastDay="2025-09-26",
                 holidays=["2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-12",
                           "2025-09-15", "2025-09-16", "2025-09-17", "2025-09-18", "2025-09-19"])
    bs3 = bells.BellSchedule([data3], {"role": "student"})
    v3 = cv.build_calendar(bs3, data3, [{"title": "A", "weeks": 1, "lessons": []},
                                        {"title": "B", "weeks": 1, "lessons": []}])
    brk = next(u for u in v3["units"] if u.get("break_section"))
    assert brk["rows"][0]["days"] == 16, brk

    # A named long weekend (Fri+Mon off) is boxed AND its days stay greyed in the
    # weeks; a lone mid-week day off is NOT boxed (just greyed).
    data4 = dict(DATA, lastDay="2025-09-19",
                 holidays=["2025-09-05", "2025-09-08"],   # Fri + Mon = a long weekend
                 breakNames={"2025-09-08": "Long weekend"})
    bs4 = bells.BellSchedule([data4], {"role": "student"})
    v4 = cv.build_calendar(bs4, data4, [{"title": "A", "weeks": 3, "lessons": []}])
    rows = v4["units"][0]["rows"]
    brk4 = [r for r in rows if r["kind"] == "break"]
    assert len(brk4) == 1 and brk4[0]["name"] == "Long weekend", rows
    assert brk4[0]["days"] == 4, brk4   # Sep 5 (Fri) .. Sep 8 (Mon)

    data5 = dict(DATA, lastDay="2025-09-19", holidays=["2025-09-10"])  # lone Wednesday
    bs5 = bells.BellSchedule([data5], {"role": "student"})
    v5 = cv.build_calendar(bs5, data5, [{"title": "A", "weeks": 3, "lessons": []}])
    assert not any(r["kind"] == "break" for r in v5["units"][0]["rows"]), \
        "a lone mid-week day off should be a greyed cell, not a break box"

    print("ok - all calendar_view checks passed")


if __name__ == "__main__":
    sys.exit(main())
