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


def _fmt_year(year):
    """'2026-2027' -> '2026-27'; passes through anything unexpected."""
    parts = (year or "").split("-")
    if len(parts) == 2 and len(parts[1]) >= 2:
        return f"{parts[0]}-{parts[1][-2:]}"
    return year or ""


def _fmt_range(a, b):
    """A compact human date range, e.g. 'Aug 18-22' or 'Aug 25-Sep 2'."""
    if a == b:
        return a.strftime("%b %-d")
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.strftime('%b %-d')}–{b.strftime('%-d')}"
    return f"{a.strftime('%b %-d')}–{b.strftime('%b %-d')}"


def _weeks(bs, data, start, end):
    """The year as an ordered list of items: teaching weeks and break spans.

    A teaching week is a Monday-anchored calendar week with >=1 school day:
    {is_break: False, monday, days: [school dates], number}. A run of consecutive
    calendar weeks with NO school days is collapsed into ONE break that spans the
    whole gap -- from the day after the last school day before it to the day before
    the first school day after it, so adjacent weekends (which the calendar's
    `holidays` never lists, since they're never school days) are included:
    {is_break: True, start, end, name}. Teaching weeks are numbered 1..n."""
    break_names = {_d(k): v for k, v in (data.get("breakNames") or {}).items()}
    raw, cur_key = [], None
    d = start
    while d <= end:
        key = d.isocalendar()[:2]
        if key != cur_key:
            raw.append({"monday": d - timedelta(days=d.weekday()), "days": []})
            cur_key = key
        if bs.is_school_day(d):
            raw[-1]["days"].append(d)
        d += timedelta(days=1)
    for w in raw:
        w["is_break"] = not w["days"]

    # Number teaching weeks, then collapse runs of empty weeks into break spans.
    n = 0
    for w in raw:
        if not w["is_break"]:
            n += 1
            w["number"] = n
    out, i = [], 0
    while i < len(raw):
        w = raw[i]
        if not w["is_break"]:
            out.append(w)
            i += 1
            continue
        j = i
        while j + 1 < len(raw) and raw[j + 1]["is_break"]:
            j += 1
        # The span runs school-day to school-day. previous_/next_school_day scan
        # day-by-day, so from any non-school day inside the break they jump over the
        # whole stretch (incl. weekends the `holidays` array never lists) to the
        # bordering school days; the break is the days strictly between them.
        inside = raw[i]["monday"]                         # a non-school day in the break
        span_start = (bs.previous_school_day(inside) + timedelta(days=1)) if i > 0 \
            else raw[i]["monday"]
        span_end = (bs.next_school_day(inside) - timedelta(days=1)) if j + 1 < len(raw) \
            else raw[j]["monday"] + timedelta(days=6)
        names = [break_names[span_start + timedelta(days=k)]
                 for k in range((span_end - span_start).days + 1)
                 if (span_start + timedelta(days=k)) in break_names]
        out.append({"is_break": True, "start": span_start, "end": span_end,
                    "name": names[0] if names else "Break"})
        i = j + 1
    return out


def _week_cells(week, assign):
    """The week's five weekday (Mon-Fri) columns as cells. A multi-day lesson on
    consecutive days is one block spanning those columns; free and 'off' days are
    one box per day (so unfilled days read individually). 'off' is a weekend/
    holiday/outside-the-term day, kept so every week aligns to its day-of-week
    column. `days` is the column span."""
    schooldays = set(week["days"])
    slots = []
    for i in range(5):  # Monday .. Friday
        d = week["monday"] + timedelta(days=i)
        if d in schooldays:
            title = assign.get(d)
            slots.append(("lesson", title) if title else ("free", None))
        else:
            slots.append(("off", None))
    cells = []
    for kind, title in slots:
        # Only contiguous days of the SAME lesson merge into one block.
        if kind == "lesson" and cells and cells[-1]["kind"] == "lesson" \
                and cells[-1]["title"] == title:
            cells[-1]["days"] += 1
        else:
            cells.append({"kind": kind, "title": title, "days": 1})
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
            have += 0 if w["is_break"] else len(w["days"])
        # Trim trailing pure-break weeks we may have grabbed.
        while taken and taken[-1]["is_break"]:
            taken.pop(); idx -= 1
    return taken, idx, derived


def _break_row(w):
    return {"kind": "break", "name": w["name"], "range": _fmt_range(w["start"], w["end"])}


def build_calendar(bs, data, units):
    """Lay `units` (ordered [{title, weeks, lessons:[{title, days}]}]) onto the
    school year of (bs, data) -- its full firstDay..lastDay span. Returns a view
    model whose `units` list interleaves real units with standalone break sections:

    {warnings: [str],
     units: [{title, weeks, derived, overflow:[{title,days,fit}], free_days,
              rows: [{kind:'week', number, range, school_days, cells:[...]}
                   | {kind:'break', name, range}]}            # a unit; mid-unit
                                                              # breaks stay in rows
            | {break_section: True, rows: [{kind:'break', ...}]}]}  # breaks BETWEEN
                                                                   # units, own section
    """
    start = _d(data["firstDay"])
    end = _d(data["lastDay"])
    weeks = _weeks(bs, data, start, end)
    teaching_total = sum(1 for w in weeks if not w["is_break"])

    out_units, idx, weeks_used = [], 0, 0
    for unit in units:
        # Breaks before this unit (i.e. between units) become their own section;
        # breaks that fall once the unit is underway stay inline (see _consume).
        lead = []
        while idx < len(weeks) and weeks[idx]["is_break"]:
            lead.append(weeks[idx])
            idx += 1
        if lead:
            out_units.append({"break_section": True, "rows": [_break_row(w) for w in lead]})

        taken, idx, derived = _consume(weeks, idx, unit)
        weeks_used += sum(1 for w in taken if not w["is_break"])

        # Lay this unit's lessons into its school days, in order.
        sdays = [d for w in taken if not w["is_break"] for d in w["days"]]
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
                rows.append(_break_row(w))
            else:
                rows.append({"kind": "week", "number": w["number"],
                             "range": _fmt_range(w["days"][0], w["days"][-1]),
                             "school_days": len(w["days"]),
                             "cells": _week_cells(w, assign)})
        out_units.append({"break_section": False, "title": unit["title"],
                          "weeks": unit["weeks"], "derived": derived, "overflow": overflow,
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
        if u.get("overflow"):   # break sections have no overflow
            n = sum(o["days"] - o["fit"] for o in u["overflow"])
            warnings.append(f"Unit “{u['title']}” overflows by {n} lesson-day(s).")

    return {"warnings": warnings, "units": out_units,
            "teaching_weeks": teaching_total, "calendar_name": data.get("name", ""),
            "year": _fmt_year(data.get("year"))}
