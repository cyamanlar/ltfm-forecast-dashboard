"""Participation and scoring (identical eligible cases for me and for persistence).

Skill % = 100 × (1 − my MAE ÷ persistence MAE). The four variables are never added together.
"""
from __future__ import annotations

import math
import re

from .observations import avg
from .timeutil import day_end, deadline, finite, now_ms, parse_ts, pr_window, valid_date


def valid_vector(v) -> bool:
    if not isinstance(v, list) or len(v) != 4:
        return False
    if not all(x is None or finite(x) for x in v):
        return False
    if finite(v[0]) and finite(v[1]) and v[0] < v[1]:
        return False
    return all(x is None or x >= 0 for x in v[2:])


def complete(r) -> bool:
    return bool(r) and valid_vector(r.get("f")) and all(finite(x) for x in r["f"])


def ontime(r) -> bool:
    if not r or not r.get("submitted"):
        return False
    t = parse_ts(r["submitted"])
    return t is not None and t <= deadline(r["date"])


def unique_records(records):
    seen, out = set(), []
    for r in records:
        if r["date"] not in seen:
            seen.add(r["date"])
            out.append(r)
    return out


def participation(calendar, records, time=None):
    time = now_ms() if time is None else time
    dates = sorted({d for d in calendar if valid_date(d)})
    dset = set(dates)
    rows = [r for r in unique_records(records) if r["date"] in dset]
    elapsed = {d for d in dates if deadline(d) < time}
    valid = [r for r in rows if complete(r) and ontime(r) and parse_ts(r["submitted"]) <= time]
    done = len([r for r in valid if r["date"] in elapsed])
    required = math.ceil(len(dates) * 7 / 10)
    return {"total": len(dates), "elapsed": len(elapsed), "valid": len(valid), "done": done, "required": required,
            "remaining": max(0, required - len(valid)),
            "rate": 100 * done / len(elapsed) if elapsed else None,
            "ended": bool(dates) and len(elapsed) == len(dates),
            "met": bool(dates) and len(valid) >= required}


def participation_text(p) -> str:
    if not p["total"]:
        return "N/A — starts with your first submitted forecast"
    if not p["elapsed"]:
        return "No deadline has passed yet: your first forecast counts after 21:00 today"
    if p["ended"]:
        return "Requirement met" if p["met"] else "Requirement not met"
    return "On track — at least 70% to date" if p["rate"] >= 70 else "Below 70% — participation must improve"


def _src2(r, field):
    s = r.get(field)
    if isinstance(s, list):
        return s[2] if len(s) > 2 else ""
    parts = str(s or "").split(" | ")
    return parts[2] if len(parts) > 2 else ""


def exclusion(r, k, time=None, calendar=None) -> str:
    time = now_ms() if time is None else time
    if calendar is not None and r["date"] not in calendar:
        return "Not scheduled"
    if not r.get("submitted"):
        return "Not submitted"
    if parse_ts(r["submitted"]) > time:
        return "Submission is after current time"
    if not ontime(r):
        return "Late"
    if not complete(r):
        return "Incomplete forecast"
    if day_end(r["date"]) >= time:
        return "Target day not complete"
    if k == 2 and pr_window(r["date"])[1] > time:
        return "Precipitation day (09:00→09:00) not complete"
    o = r.get("o")
    if not o or not finite(o[k]):
        return "Observation missing"
    if k == 2 and re.search("Model analysis", _src2(r, "oSrc"), re.I):
        return "Model analysis is not an observation"
    b = r.get("b")
    if not valid_date(r.get("baselineDate")) or not b or not finite(b[k]):
        return "Persistence missing"
    if k == 2 and re.search("Model analysis", _src2(r, "bSrc"), re.I):
        return "Persistence precipitation from a model analysis is not used"
    bc = parse_ts(r.get("baselineCaptured") or "")
    if bc is None or bc > parse_ts(r["submitted"]) or day_end(r["baselineDate"]) >= bc:
        return "Persistence not available before submission"
    return ""


def scores(rows, k, time=None, calendar=None):
    pairs = sorted([r for r in unique_records(rows) if not exclusion(r, k, time, calendar)], key=lambda r: r["date"])
    errors = [r["f"][k] - r["o"][k] for r in pairs]
    mae = avg([abs(e) for e in errors])
    base = avg([abs(r["b"][k] - r["o"][k]) for r in pairs])
    w = t = l = 0
    for r in pairs:
        diff = abs(r["f"][k] - r["o"][k]) - abs(r["b"][k] - r["o"][k])
        if abs(diff) < 1e-9:
            t += 1
        elif diff < 0:
            w += 1
        else:
            l += 1
    skill = 100 * (1 - mae / base) if finite(base) and base > 0 else None
    win_points = 100 * (w + .5 * t) / len(pairs) if pairs else None
    skill_points = max(0, min(100, skill)) if finite(skill) else None
    overall = None if skill_points is None or win_points is None else .5 * skill_points + .5 * win_points
    return {"n": len(pairs), "excluded": len(unique_records(rows)) - len(pairs), "mae": mae, "bias": avg(errors),
            "base": base, "skill": skill, "w": w, "t": t, "l": l, "winPoints": win_points,
            "skillPoints": skill_points, "overall": overall, "pairs": pairs}


def clim_scores(rows, k, time=None, calendar=None):
    pairs = [r for r in scores(rows, k, time, calendar)["pairs"] if r.get("c") and finite(r["c"][k])]
    mae = avg([abs(r["f"][k] - r["o"][k]) for r in pairs])
    cm = avg([abs(r["c"][k] - r["o"][k]) for r in pairs])
    return {"n": len(pairs), "mae": mae, "cmae": cm, "skill": 100 * (1 - mae / cm) if finite(cm) and cm > 0 else None}


def model_scores(rows, k, time=None, calendar=None):
    pairs = scores(rows, k, time, calendar)["pairs"]
    names = sorted({m for r in pairs for m in (r.get("models") or {})})
    out = []
    for m in names:
        p = [r for r in pairs if (r.get("models") or {}).get(m) and finite(r["models"][m][k])]
        e = [r["models"][m][k] - r["o"][k] for r in p]
        out.append({"model": m, "n": len(p), "mae": avg([abs(x) for x in e]), "bias": avg(e),
                    "me": avg([abs(r["f"][k] - r["o"][k]) for r in p])})
    return sorted(out, key=lambda x: x["mae"] if finite(x["mae"]) else 1e9)


def skill_series(rows, k, time=None, calendar=None):
    p = scores(rows, k, time, calendar)["pairs"]
    out, sm, sb = [], 0.0, 0.0
    for i, r in enumerate(p):
        sm += abs(r["f"][k] - r["o"][k])
        sb += abs(r["b"][k] - r["o"][k])
        win = p[max(0, i - 6):i + 1]
        wm = avg([abs(x["f"][k] - x["o"][k]) for x in win])
        wb = avg([abs(x["b"][k] - x["o"][k]) for x in win])
        out.append({"date": r["date"], "cum": 100 * (1 - sm / sb) if sb > 0 else None,
                    "roll": 100 * (1 - wm / wb) if len(win) >= 3 and wb > 0 else None,
                    "abs": abs(r["f"][k] - r["o"][k]), "ma": wm})
    return out


def rec_status(r) -> str:
    if not r:
        return "Missed"
    if not r.get("submitted"):
        return "Draft / not submitted"
    return ("On time" if ontime(r) else "LATE") + ("" if complete(r) else " / incomplete")
