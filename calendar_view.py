"""Lay a course outline out across a real school year for the calendar view.

The outline is the source of truth: each unit consumes a number of *school
weeks* (calendar weeks with at least one school day) -- "loosely", so a 2-week
unit takes the next 2 school weeks regardless of days off, and a week with a
holiday in it still counts as one week. Full-week breaks are shown between units
but don't count. Within a unit's weeks, lessons are laid into the school days in
order (each lesson takes its `days`, default 1).

School days / holidays / breaks come from the `bells` library + a bhs-calendars
JSON. This module is pure (no Flask, no SQL): the app hands it the outline data
and a calendar; it returns a view model the template renders.
"""

import json
import math
import os
from datetime import date, timedelta

import bells


def load_calendar(calendar_id, calendar_dir):
    """Load a bells calendar by id from `calendar_dir`, returning
    (BellSchedule, raw_data). Raises FileNotFoundError if the JSON is absent.

    Exam days come from the bells calendar itself, read via
    `BellSchedule.non_class_label(date)` (bells normalizes both a named EXAMS bell
    schedule and a raw `nonClassDays` entry to a label like "exam"). The AP exam
    window and grading-period closes are carried in the calendar's first-class
    `annotations` field and read back via the bells annotation API (see
    `build_calendar`), so no out-of-band sidecar is needed."""
    path = os.path.join(calendar_dir, f"{calendar_id}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return bells.BellSchedule([data], {"role": "student"}), data


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _fmt_num(n):
    """Drop a trailing '.0' so whole numbers read cleanly (40.0 -> '40'), but keep
    a real fraction (1.5 -> '1.5')."""
    return str(int(n)) if isinstance(n, float) and n.is_integer() else str(n)


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
    """The year as an ordered list of items: school weeks and break boxes.

    A school week is a Monday-anchored calendar week with >=1 school day:
    {is_break: False, monday, days: [school dates], number}. A break box is any gap
    between consecutive school days that has a non-school WEEKDAY and crosses a
    weekend -- i.e. a real holiday/break, not a plain weekend and not a lone
    mid-week day off (which stays a greyed cell in its week). This boxes named long
    weekends (e.g. Presidents' Day) as well as week-plus breaks; the weekday(s) off
    still show greyed in their school weeks. {is_break: True, name, days} where
    `days` is the break's length in days; named from breakNames, else "Break"."""
    break_names = {_d(k): v for k, v in (data.get("breakNames") or {}).items()}

    school_days = []
    d = start
    while d <= end:
        if bs.is_school_day(d):
            school_days.append(d)
        d += timedelta(days=1)

    # Group school days into Monday-anchored school weeks. The week *number* is
    # bells' canonical school-week numbering (same Monday-anchored, break-skipping
    # algorithm this layout uses), so "week 9" agrees with the calendar's own
    # `weeks` annotations by construction; fall back to local 1..n if a week is
    # somehow missing upstream. (test_calendar_view asserts the two agree.)
    bells_number = {w["monday"]: w["number"] for w in bs.school_weeks()}
    weeks_by_key, order = {}, []
    for sd in school_days:
        key = sd.isocalendar()[:2]
        if key not in weeks_by_key:
            weeks_by_key[key] = {"is_break": False, "monday": sd - timedelta(days=sd.weekday()),
                                 "days": []}
            order.append(weeks_by_key[key])
        weeks_by_key[key]["days"].append(sd)
    for n, w in enumerate(order, 1):
        w["number"] = bells_number.get(w["monday"], n)

    # Breaks: a weekend-crossing gap that is NAMED (a breakNames entry, e.g. a long
    # weekend like Presidents' Day) or long enough to be a week+ break (>=5 weekdays
    # off, so a multi-week break is shown even if unnamed). A plain weekend or a
    # lone unnamed day off is NOT boxed -- it just shows greyed in its week.
    breaks = []
    for a, b in zip(school_days, school_days[1:]):
        gap = [a + timedelta(days=k) for k in range(1, (b - a).days)]
        crosses_weekend = any(g.weekday() >= 5 for g in gap)
        weekdays_off = sum(1 for g in gap if g.weekday() < 5)
        named = [break_names[g] for g in gap if g in break_names]
        if crosses_weekend and (named or weekdays_off >= 5):
            breaks.append({"is_break": True, "start": gap[0],
                           "name": named[0] if named else "Break", "days": len(gap)})

    # Merge chronologically: each break sits before the school week that follows it.
    out, bi = [], 0
    for w in order:
        while bi < len(breaks) and breaks[bi]["start"] < w["days"][0]:
            out.append(breaks[bi])
            bi += 1
        out.append(w)
    out.extend(breaks[bi:])
    return out


def _week_cells(week, assign, labels):
    """The week's five weekday (Mon-Fri) columns as cells. A multi-day lesson on
    consecutive days is one block spanning those columns; free and 'off' days are
    one box per day (so unfilled days read individually). 'off' is a weekend/
    holiday/outside-the-term day, kept so every week aligns to its day-of-week
    column. A school day carrying a non-class label (`labels[d]`) renders as an
    'exam' cell (label 'exam') or a generic 'special' cell (any other label)
    instead of a lesson/free slot; consecutive same-label days merge like a
    lesson does. `days` is the column span. `assign` maps a date to its lesson
    dict ({node_id, title, days}); a lesson cell carries `node_id` and the
    lesson's own `lesson_days` (its outline duration) so the view can edit it."""
    schooldays = set(week["days"])
    slots = []
    for i in range(5):  # Monday .. Friday
        d = week["monday"] + timedelta(days=i)
        if d in schooldays:
            label = labels.get(d)
            if label:
                slots.append(("exam" if label == "exam" else "special", label, None))
            else:
                L = assign.get(d)
                slots.append(("lesson", L["title"], L) if L else ("free", None, None))
        else:
            slots.append(("off", None, None))
    cells = []
    for kind, title, lesson in slots:
        node_id = lesson["node_id"] if lesson else None
        # Only contiguous days of the SAME lesson (by node) or same special label merge.
        if kind in ("lesson", "exam", "special") and cells and cells[-1]["kind"] == kind \
                and cells[-1]["title"] == title and cells[-1].get("node_id") == node_id:
            cells[-1]["days"] += 1
        else:
            cell = {"kind": kind, "title": title, "days": 1}
            if lesson:
                cell["node_id"] = node_id
                cell["lesson_days"] = lesson["days"]
            cells.append(cell)
    return cells


def _consume(weeks, idx, unit, greedy=False, max_idx=None):
    """Consume the school weeks one unit gets, starting at weeks[idx]. A unit with
    an explicit `weeks` count takes that many SCHOOL weeks (breaks pass through,
    uncounted). A unit with no count takes just enough school weeks to hold its
    lessons' days (min one) -- unless `greedy`, in which case it takes ALL remaining
    weeks to the end of the year (the last outline unit, so a no-week-count final
    unit becomes a real, lesson-holding "rest of the year" catch-all). Returns
    (unit_weeks, next_idx, derived).

    `max_idx` caps how far into `weeks` the unit may reach -- the start index of the
    next pinned unit, or len(weeks). A unit that hits the cap stops there, so its
    unplaced lessons overflow: this is how "too many units before a pinned unit" is
    rendered (the segment runs out of room and the tail units overflow)."""
    limit = len(weeks) if max_idx is None else min(len(weeks), max_idx)
    taken, derived = [], False
    if unit["weeks"]:
        remaining = unit["weeks"]
        while remaining > 0 and idx < limit:
            w = weeks[idx]; idx += 1
            taken.append(w)
            if not w["is_break"]:
                remaining -= 1
    else:
        derived = True
        need = max(1, sum(L["days"] for L in unit["lessons"]))
        have = 0
        # Greedy (last unit): take everything left. Otherwise take at least one
        # school week and keep going until the lessons fit -- but never past `limit`.
        while idx < limit and (greedy or have == 0 or have < need):
            w = weeks[idx]; idx += 1
            taken.append(w)
            have += 0 if w["is_break"] else len(w["days"])
        # Trim trailing pure-break weeks we may have grabbed.
        while taken and taken[-1]["is_break"]:
            taken.pop(); idx -= 1
    return taken, idx, derived


def _break_row(w):
    return {"kind": "break", "name": w["name"], "days": w["days"]}


def _week_badges(bs, number):
    """Resolve a school week's calendar badges from the bells annotation API:
    `(is_ap, is_ib, grading_close)`. `is_ap`/`is_ib` are True when an `apExams`/
    `ibExams` range annotation overlaps the week's school days; `grading_close` is
    the label of a `weeks[n]` annotation on the week (None if absent). All come from
    `bs.annotations_for_week(number)`, which tags each hit with its `source`."""
    anns = bs.annotations_for_week(number)
    is_ap = any(a.get("source") == "range"
                and "apExams" in (a.get("id"), a.get("kind")) for a in anns)
    is_ib = any(a.get("source") == "range"
                and "ibExams" in (a.get("id"), a.get("kind")) for a in anns)
    grading_close = next((a.get("label") for a in anns if a.get("source") == "week"), None)
    return is_ap, is_ib, grading_close


def _requested_weeks(units, weeks):
    """Total school weeks the units demand -- laid out over the year's real
    school weeks at their true positions, so an auto-sized unit is sized by the
    ACTUAL school days where it starts (a 5-lesson unit starting in a 4-day week
    needs 2 weeks), continuing with nominal 5-day weeks past year-end so an
    overflow is still measured (the live layout caps at availability, hiding it).

    Rules: an explicit count is taken verbatim (0 = removed from the calendar); an
    auto-sized unit takes at least 1 week, and otherwise as many as its lessons
    need; the last auto-sized unit is greedy (absorbs the remainder) so it never
    over-asks and isn't counted."""
    school = [len(w["days"]) for w in weeks if not w["is_break"]]   # days per school week
    days_at = lambda p: school[p] if p < len(school) else 5         # nominal 5 past year-end
    emitted = [i for i, u in enumerate(units) if u["weeks"] != 0]
    last_emit = emitted[-1] if emitted else None
    total, pos = 0, 0
    for i, u in enumerate(units):
        w = u["weeks"]
        if w == 0:                               # removed from the calendar
            continue
        if w:                                    # explicit count
            total += w
            pos += math.ceil(w)
        elif i == last_emit:                     # greedy last auto unit: absorbs the rest
            break
        else:                                    # auto: weeks to hold its lessons from here
            need = sum(int(L["days"]) for L in u["lessons"] if int(L["days"]) > 0)
            have = taken = 0
            while taken == 0 or have < need:     # >= 1 week
                have += days_at(pos)
                pos += 1
                taken += 1
            total += taken
    return total


def build_calendar(bs, data, units):
    """Lay `units` (ordered [{title, weeks, lessons:[{title, days}]}]) onto the
    school year of (bs, data) -- its full firstDay..lastDay span. Returns a view
    model whose `units` list interleaves real units with standalone break sections:

    {warnings: [str],
     units: [{title, weeks, derived, unplanned, overflow:[{title,days,fit}], free_days,
              rows: [{kind:'week', number, range, school_days, cells:[...]}
                   | {kind:'break', name, days}]}              # a unit; mid-unit
                                                               # breaks stay in rows
            | {break_section: True, rows: [{kind:'break', ...}]}]}  # breaks BETWEEN
                                                                   # units, own section

    The last outline unit, if it carries no explicit week count, greedily takes all
    remaining weeks to year-end (a real, lesson-holding catch-all). Otherwise any
    weeks the units leave unclaimed are emitted as ONE `unplanned: True` pseudo-unit
    (its `weeks` is the leftover count, no `node_id`), so the calendar still runs to
    June.
    """
    start = _d(data["firstDay"])
    end = _d(data["lastDay"])
    weeks = _weeks(bs, data, start, end)
    school_total = sum(1 for w in weeks if not w["is_break"])

    # Exam days (and any other non-class label) come from the bells calendar itself:
    # bs.non_class_label(d) returns a label like "exam" for an in-session day that's
    # reserved (e.g. finals -- whether the source marks it via a named EXAMS schedule
    # or a raw nonClassDays entry; bells normalizes both). Such days stay in their
    # school week but are NOT bookable -- lessons flow around them -- and render as
    # their own cell kind ("exam" -> red, any other label -> a generic special cell).
    labels = {}
    for w in weeks:
        for d in (w["days"] if not w["is_break"] else []):
            lab = bs.non_class_label(d)
            if lab:
                labels[d] = lab
    # AP/IB-exam weeks and grading-period closes come from the calendar's first-class
    # `annotations`, read back per school week via the bells annotation API: a week is
    # `is_ap`/`is_ib` when an `apExams`/`ibExams` range annotation overlaps its school
    # days, and its `grading_close` is the label of any `weeks[n]` annotation on it.
    # `_week_badges` resolves all three for a given school-week number.

    out_units = []
    idx = [0]   # boxed so the nested helpers can advance it

    def emit_leading_breaks():
        # Breaks before the next unit (i.e. between units) get their own section;
        # breaks once a unit is underway stay inline in it (see _consume).
        lead = []
        while idx[0] < len(weeks) and weeks[idx[0]]["is_break"]:
            lead.append(weeks[idx[0]])
            idx[0] += 1
        if lead:
            out_units.append({"break_section": True, "rows": [_break_row(w) for w in lead]})

    def emit_unit(unit, unplanned=False, greedy=False, max_idx=None):
        taken, idx[0], derived = _consume(weeks, idx[0], unit, greedy=greedy, max_idx=max_idx)
        # Labeled days (exams etc.) aren't bookable -- exclude them so lessons
        # flow onto the unit's plain school days only.
        sdays = [d for w in taken if not w["is_break"] for d in w["days"] if d not in labels]
        assign, overflow, i = {}, [], 0
        for L in unit["lessons"]:
            need = int(L["days"])
            if need <= 0:
                continue   # an explicit 0-day lesson is omitted from the calendar
            fit = min(need, len(sdays) - i)
            for k in range(fit):
                assign[sdays[i + k]] = L
            i += fit
            if fit < need:
                overflow.append({"title": L["title"], "days": need, "fit": fit})
        rows = []
        for w in taken:
            if w["is_break"]:
                rows.append(_break_row(w))
            else:
                is_ap, is_ib, grading_close = _week_badges(bs, w["number"])
                rows.append({"kind": "week", "number": w["number"],
                             "range": _fmt_range(w["days"][0], w["days"][-1]),
                             "school_days": len(w["days"]),
                             "is_ap": is_ap,
                             "is_ib": is_ib,
                             "grading_close": grading_close,
                             "cells": _week_cells(w, assign, labels)})
        # A real unit (one with a node_id) with leftover school days: mark its FIRST
        # free cell `addable` -- clicking it in the calendar drops a new lesson into
        # the unit, which (lessons flow front-to-back) lands on exactly that day. The
        # later free cells aren't marked: a lesson can't be placed past a gap, so only
        # the first free box honestly previews where a new lesson would go.
        if unit.get("node_id"):
            for w in rows:
                first = next((c for c in w.get("cells", []) if c["kind"] == "free"), None)
                if first:
                    first["addable"] = True
                    break
        weeks_shown = sum(1 for w in taken if not w["is_break"])
        # A real unit that a FOLLOWING PIN cut short got fewer school weeks than it
        # asked for -- but the weeks pill still shows the requested count, so without
        # an explicit flag the unit is silently shrunk. (max_idx < len(weeks) means the
        # cap was a pin; a year-end cap is already covered by the over-subscription
        # warning + the Unplanned tail.) `weeks_short` is the deficit: the requested
        # span minus what fit for an explicit-week unit, or 1 for an auto unit squeezed
        # out entirely (its lessons, if any, also overflow).
        weeks_short = 0
        if max_idx is not None and max_idx < len(weeks) and not unplanned:
            if unit["weeks"]:
                weeks_short = max(0, math.ceil(unit["weeks"]) - weeks_shown)
            elif weeks_shown == 0:
                weeks_short = 1
        out_units.append({"break_section": False, "unplanned": unplanned,
                          "node_id": unit.get("node_id"), "title": unit["title"],
                          "weeks": unit["weeks"], "derived": derived, "overflow": overflow,
                          "pin": unit.get("pin"), "weeks_short": weeks_short,
                          "weeks_shown": weeks_shown,
                          "free_days": len(sdays) - i, "rows": rows})

    # --- Pins: resolve each pinned unit to the school-week INDEX it must START at.
    # `weeknum_index` maps a bells school-week number to its index in `weeks`; an
    # 'end' pin walks back the unit's span from the end week. An unresolvable week
    # (past year-end, or a break) drops the pin with a warning (sequential fallback).
    pin_warnings = []
    weeknum_index = {w["number"]: i for i, w in enumerate(weeks) if not w["is_break"]}

    def resolve_anchor(unit):
        pin = unit.get("pin")
        if not pin:
            return None
        end_i = weeknum_index.get(pin["week"])
        if end_i is None:
            pin_warnings.append(f"Unit “{unit['title']}” is pinned to week "
                                f"{pin['week']}, which isn’t a school week; ignoring the pin.")
            return None
        if pin["edge"] == "start":
            return end_i
        # 'end': walk back over school weeks from end_i until the unit's span is met.
        if unit["weeks"]:
            need, by = math.ceil(unit["weeks"]), "weeks"
        else:
            need, by = max(1, sum(L["days"] for L in unit["lessons"])), "days"
        have, t, i = 0, end_i, end_i
        while i >= 0:
            if not weeks[i]["is_break"]:
                have += 1 if by == "weeks" else len(weeks[i]["days"])
                t = i
                if have >= need:
                    break
            i -= 1
        return t

    # The last EMITTED no-count unit absorbs all remaining weeks to year-end -- a
    # first-class, lesson-holding catch-all rather than the synthetic tail below.
    # (A unit explicitly set to 0 weeks is omitted from the calendar entirely.)
    emitted = [i for i, u in enumerate(units) if u["weeks"] != 0]
    last_emit = emitted[-1] if emitted else None

    # Anchor start index per emitted, pinned, resolvable unit; and each unit's
    # boundary = the start index of the NEXT anchored unit (else year-end). A unit
    # can't reach past its boundary, so a segment's surplus units overflow at the pin.
    anchor = {i: resolve_anchor(units[i]) for i in emitted}
    anchor = {i: t for i, t in anchor.items() if t is not None}
    next_anchor_T, nxt = {}, len(weeks)
    for i in reversed(range(len(units))):
        next_anchor_T[i] = nxt
        if i in anchor:
            nxt = anchor[i]

    def advance_to(target):
        # Emit any breaks at the cursor, then an "Unplanned" gap so the cursor
        # reaches `target` (the next pin's start) -- slack the units left before it.
        emit_leading_breaks()
        gap = sum(1 for w in weeks[idx[0]:target] if not w["is_break"])
        if gap > 0:
            emit_unit({"title": "Unplanned", "weeks": gap, "lessons": []}, unplanned=True)
        emit_leading_breaks()

    for i, unit in enumerate(units):
        if unit["weeks"] == 0:
            continue
        if i in anchor:
            T = anchor[i]
            if idx[0] < T:
                advance_to(T)              # slack before the pin -> unplanned gap
            if idx[0] > T:                 # earlier units overran this pin
                pin_warnings.append(f"Unit “{unit['title']}” is pinned to week "
                    f"{unit['pin']['week']} but earlier units overrun it; "
                    f"placing it as soon as there’s room.")
            else:
                idx[0] = T
            emit_leading_breaks()
            emit_unit(unit, max_idx=next_anchor_T[i])   # a pinned unit is never greedy
        else:
            emit_leading_breaks()
            greedy = (i == last_emit and not unit["weeks"])
            emit_unit(unit, greedy=greedy, max_idx=next_anchor_T[i])

    # Run the calendar out to the end of the year: any school weeks the units
    # didn't claim go in ONE synthetic "Unplanned" section (header shows the count).
    # A greedy last unit will have eaten them, so this only fires when the final unit
    # had an explicit week count that left a tail.
    emit_leading_breaks()
    remaining = sum(1 for w in weeks[idx[0]:] if not w["is_break"])
    if remaining:
        emit_unit({"title": "Unplanned", "weeks": remaining, "lessons": []}, unplanned=True)
    emit_leading_breaks()   # any trailing breaks

    warnings = list(pin_warnings)
    requested = _requested_weeks(units, weeks)
    if requested > school_total:
        warnings.append(f"Units ask for more weeks than the year has "
                        f"({_fmt_num(requested)} vs {school_total} school weeks).")
    for u in out_units:
        if u.get("weeks_short"):   # a following pin left no room for the unit's span
            if u["weeks"]:
                warnings.append(f"Unit “{u['title']}” needs {_fmt_num(u['weeks'])} weeks "
                                f"but only {u['weeks_shown']} fit before the next pinned "
                                f"unit.")
            else:
                warnings.append(f"Unit “{u['title']}” doesn’t fit before the next "
                                f"pinned unit.")
        if u.get("overflow"):   # break sections have no overflow
            n = sum(o["days"] - o["fit"] for o in u["overflow"])
            warnings.append(f"Unit “{u['title']}” overflows by {n} lesson-day(s).")

    return {"warnings": warnings, "units": out_units,
            "school_weeks": school_total, "calendar_name": data.get("name", ""),
            "year": _fmt_year(data.get("year"))}
