"""Lay a course outline out across a real school year for the calendar view.

The outline is the source of truth: each unit consumes a number of *teaching
weeks* (calendar weeks with at least one school day) -- "loosely", so a 2-week
unit takes the next 2 teaching weeks regardless of days off, and a week with a
holiday in it still counts as one week. Full-week breaks are shown between units
but don't count. Within a unit's weeks, lessons are laid into the school days in
order (each lesson takes its `days`, default 1).

School days / holidays / breaks come from the `bells` library + a bhs-calendars
JSON. This module is pure (no Flask, no SQL): the app hands it the outline data
and a calendar; it returns a view model the template renders.
"""

import json
import os
from datetime import date, timedelta

import bells


def load_calendar(calendar_id, calendar_dir):
    """Load a bells calendar by id from `calendar_dir`, returning
    (BellSchedule, raw_data). Raises FileNotFoundError if the JSON is absent."""
    path = os.path.join(calendar_dir, f"{calendar_id}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return bells.BellSchedule([data], {"role": "student"}), data


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _fmt_range(a, b):
    """A compact human date range, e.g. 'Aug 18-22' or 'Aug 25-Sep 2'."""
    if a == b:
        return a.strftime("%b %-d")
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.strftime('%b %-d')}–{b.strftime('%-d')}"
    return f"{a.strftime('%b %-d')}–{b.strftime('%b %-d')}"


def _weeks(bs, data, start, end):
    """The year as an ordered list of calendar weeks (Monday-anchored), each:
    {monday, days: [school dates], is_break, name, number}. A week with no school
    days is a break (named from the calendar's breakNames when possible); teaching
    weeks are numbered 1..n."""
    break_names = {_d(k): v for k, v in (data.get("breakNames") or {}).items()}
    weeks, cur, cur_key = [], None, None
    d = start
    while d <= end:
        key = d.isocalendar()[:2]
        if key != cur_key:
            cur = {"monday": d - timedelta(days=d.weekday()), "days": []}
            weeks.append(cur)
            cur_key = key
        if bs.is_school_day(d):
            cur["days"].append(d)
        d += timedelta(days=1)

    n = 0
    for w in weeks:
        w["is_break"] = not w["days"]
        if w["is_break"]:
            w["number"] = None
            names = [break_names[w["monday"] + timedelta(days=i)]
                     for i in range(7) if (w["monday"] + timedelta(days=i)) in break_names]
            w["name"] = names[0] if names else "Break"
        else:
            n += 1
            w["number"] = n
            w["name"] = None
    return weeks


def _rle_cells(days, assign):
    """Run-length-encode a week's school days into lesson/free cells."""
    cells = []
    for d in days:
        title = assign.get(d)
        kind = "lesson" if title else "free"
        if cells and cells[-1]["kind"] == kind and cells[-1]["title"] == title:
            cells[-1]["days"] += 1
        else:
            cells.append({"title": title, "days": 1, "kind": kind})
    return cells


def _consume(weeks, idx, unit):
    """Consume the teaching weeks one unit gets, starting at weeks[idx]. A unit with
    an explicit `weeks` count takes that many TEACHING weeks (breaks pass through,
    uncounted). A unit with no count takes just enough teaching weeks to hold its
    lessons' days (min one). Returns (unit_weeks, next_idx, derived)."""
    taken, derived = [], False
    if unit["weeks"]:
        remaining = unit["weeks"]
        while remaining > 0 and idx < len(weeks):
            w = weeks[idx]; idx += 1
            taken.append(w)
            if not w["is_break"]:
                remaining -= 1
    else:
        derived = True
        need = max(1, sum(L["days"] for L in unit["lessons"]))
        have = 0
        # Always take at least one teaching week; keep going until the lessons fit.
        while idx < len(weeks) and (have == 0 or have < need):
            w = weeks[idx]; idx += 1
            taken.append(w)
            have += len(w["days"])
        # Trim trailing pure-break weeks we may have grabbed.
        while taken and taken[-1]["is_break"]:
            taken.pop(); idx -= 1
    return taken, idx, derived


def build_calendar(bs, data, units, start=None):
    """Lay `units` (ordered [{title, weeks, lessons:[{title, days}]}]) onto the
    school year of (bs, data). Returns a view model:

    {warnings: [str],
     units: [{title, weeks, derived, overflow:[{title,days,fit}], free_days,
              rows: [{kind:'week', number, range, school_days, cells:[{title,days,kind}]}
                   | {kind:'break', name, range}]}]}
    """
    start = _d(start or data["firstDay"])
    end = _d(data["lastDay"])
    weeks = _weeks(bs, data, start, end)
    teaching_total = sum(1 for w in weeks if not w["is_break"])

    out_units, idx, weeks_used = [], 0, 0
    for unit in units:
        taken, idx, derived = _consume(weeks, idx, unit)
        weeks_used += sum(1 for w in taken if not w["is_break"])

        # Lay this unit's lessons into its school days, in order.
        sdays = [d for w in taken for d in w["days"]]
        assign, overflow, i = {}, [], 0
        for L in unit["lessons"]:
            need = max(1, int(L["days"]))
            fit = min(need, len(sdays) - i)
            for k in range(fit):
                assign[sdays[i + k]] = L["title"]
            i += fit
            if fit < need:
                overflow.append({"title": L["title"], "days": need, "fit": fit})
        free_days = len(sdays) - i

        rows = []
        for w in taken:
            if w["is_break"]:
                rows.append({"kind": "break", "name": w["name"],
                             "range": _fmt_range(w["monday"], w["monday"] + timedelta(days=4))})
            else:
                rows.append({"kind": "week", "number": w["number"],
                             "range": _fmt_range(w["days"][0], w["days"][-1]),
                             "school_days": len(w["days"]),
                             "cells": _rle_cells(w["days"], assign)})
        out_units.append({"title": unit["title"], "weeks": unit["weeks"],
                          "derived": derived, "overflow": overflow,
                          "free_days": free_days, "rows": rows})

    warnings = []
    if idx < len(weeks):
        leftover = sum(1 for w in weeks[idx:] if not w["is_break"])
        if leftover:
            warnings.append(f"{leftover} teaching week(s) at the end of the year are "
                            "unscheduled (no unit covers them).")
    requested = sum(u["weeks"] for u in units if u["weeks"])
    if weeks_used > teaching_total or requested > teaching_total:
        warnings.append(f"Units ask for more weeks than the year has "
                        f"({max(requested, weeks_used)} vs {teaching_total} teaching weeks).")
    for u in out_units:
        if u["overflow"]:
            n = sum(o["days"] - o["fit"] for o in u["overflow"])
            warnings.append(f"Unit “{u['title']}” overflows by {n} lesson-day(s).")

    return {"warnings": warnings, "units": out_units,
            "teaching_weeks": teaching_total, "calendar_name": data.get("name", "")}
