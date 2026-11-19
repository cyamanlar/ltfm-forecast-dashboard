"""Daily values from METAR reports, persistence, model bias, ensemble statistics and evidence helpers."""
from __future__ import annotations

from .metar import has_precip, metar_dew_gap, parse_metar_fields
from .timeutil import (H3, HOUR, PR_SHIFT, day_end, day_start, finite, iso_local, local_day, local_ms,
                       now_ms, pr_day_of, pr_window, r1, shift, stamp)


def avg(a):
    a = [x for x in a if finite(x)]
    return sum(a) / len(a) if a else None


def quant(sorted_vals, p):
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


# ---------------------------------------------------------------- METAR daily values
def daily_from_reports(reps, d, now):
    T = [r["tmpc"] for r in reps if finite(r["tmpc"])]
    W = [r["sknt"] for r in reps if finite(r["sknt"])]
    G = [r["gust"] for r in reps if finite(r["gust"])]
    PR = [r for r in reps if has_precip(r["metar"])]
    return {"date": d, "n": len(reps), "gust": max(G) if G else None, "prN": len(PR),
            "prHours": sorted({iso_local(r["ms"])[11:13] for r in PR}),
            "tmax": max(T) if T else None, "tmin": min(T) if T else None, "wind": max(W) if W else None,
            "complete": now > day_end(d), "first": reps[0]["ms"] if reps else None,
            "last": reps[-1]["ms"] if reps else None}


def add_pr_window(od, reports, now):
    a, b = pr_window(od["date"])
    reps = [r for r in reports if a <= r["ms"] < b]
    PR = [r for r in reps if has_precip(r["metar"])]
    od["pn"] = len(reps)
    od["prN"] = len(PR)
    od["prHours"] = sorted({iso_local(r["ms"])[11:13] for r in PR})
    od["prComplete"] = now >= b
    return od


def build_obs(reports, now):
    by = {}
    for r in reports:
        by.setdefault(local_day(r["ms"]), []).append(r)
    daily = {d: add_pr_window(daily_from_reports(v, d, now), reports, now) for d, v in by.items()}
    return {"reports": reports, "daily": daily}


def obs_val(od, k):
    return None if not od else [od["tmax"], od["tmin"], None, od["wind"]][k]


def dry_day(d) -> bool:
    return bool(d and d.get("prComplete") and d.get("pn", 0) >= 40 and d.get("prN") == 0)


def compute_persistence(obs, bd, now):
    d = obs and obs["daily"].get(bd)
    if not d or not d["complete"]:
        return None
    dry = dry_day(d)
    src_m = f"METAR via IEM ({d['n']} reports)"
    pr_src = (f"No precipitation in any of {d['pn']} METARs (09:00→09:00) → 0.0 mm" if dry else
              "missing: the precipitation day ends at 09:00 today" if not d["prComplete"] else
              "missing: it rained, METAR gives no amount. Type the MGM 06–06 UTC amount from your instructor "
              "(or use the labelled model analysis)")
    return {"date": bd, "v": [d["tmax"], d["tmin"], 0 if dry else None, d["wind"]],
            "src": [src_m, src_m, pr_src, src_m], "captured": stamp(now)}


def latest_report(obs):
    if not obs or not obs["reports"]:
        return None
    reps = [r for r in obs["reports"] if r["metar"]]
    return reps[-1] if reps else obs["reports"][-1]


def closest_metar(obs, ms):
    if not obs:
        return None
    reps = [r for r in obs["reports"] if r["metar"]]
    return min(reps, key=lambda r: abs(r["ms"] - ms)) if reps else None


# ---------------------------------------------------------------- models
def live_bias(prev, obs, label, k, now=None):
    """Recent bias of a model: its day-1 forecast minus the METAR value, last 7 completed days."""
    p = prev.get(label) if prev else None
    if not p or not obs or k == 2:
        return None
    today = local_day(now or now_ms())
    e = []
    for i in range(1, 8):
        d = shift(today, -i)
        od, pv = obs["daily"].get(d), p["daily"].get(d)
        if not od or not od["complete"] or not pv or not finite(pv[k]):
            continue
        ov = obs_val(od, k)
        if finite(ov):
            e.append(pv[k] - ov)
    if not e:
        return {"n": 0, "bias": None, "mae": None}
    return {"n": len(e), "bias": avg(e), "mae": avg([abs(x) for x in e])}


def model_at(h, key, ms, max_gap=HOUR):
    if not h or not h.get("time") or not h.get(key):
        return None
    best, bd = -1, 1e15
    for i, t in enumerate(h["time"]):
        if i < len(h[key]) and finite(h[key][i]) and abs(t - ms) < bd:
            bd, best = abs(t - ms), i
    return {"v": h[key][best], "t": h["time"][best]} if best >= 0 and bd <= max_gap else None


def coach_blocks(models, m, k, target):
    """Eight 3-hour block values of one model for the target day (precipitation: 09→09)."""
    h = models.get(m, {}).get("hourly")
    if not h or not h.get("time"):
        return None
    t0 = local_ms(target) + (PR_SHIFT if k == 2 else 0)
    pre = h.get("prPrecedes", True) is not False
    out = []
    for j in range(8):
        a, b = t0 + j * H3, t0 + (j + 1) * H3
        vals = []
        for i, t in enumerate(h["time"]):
            if k == 2:
                inb = (a < t <= b) if pre else (a <= t < b)
            else:
                inb = (a <= t < b) or (k < 2 and j == 7 and t == b)
            if not inb:
                continue
            arr = h["temp"] if k < 2 else h.get("pr") if k == 2 else h["ws"]
            v = arr[i] if arr and i < len(arr) else None
            if finite(v):
                vals.append(v)
        if not vals:
            out.append(None)
        else:
            out.append(max(vals) if k in (0, 3) else min(vals) if k == 1 else r1(sum(vals)))
    ok = [x for x in out if finite(x)]
    if len(ok) < 6:
        return None
    day = max(ok) if k in (0, 3) else min(ok) if k == 1 else r1(sum(ok))
    return {"blocks": out, "day": day, "missing": 8 - len(ok)}


def reality_check(obs, models, now=None):
    """Model temperature minus METAR, last 6 hours."""
    now = now or now_ms()
    rows, warns, means = [], [], []
    if not obs:
        return {"rows": rows, "warns": warns, "mean": None}
    rec = [r for r in obs["reports"] if now - 6 * HOUR < r["ms"] <= now and finite(r["tmpc"])]
    for m, mod in models.items():
        h = mod.get("hourly")
        if not h or not h.get("time"):
            continue
        d = []
        for r in rec:
            x = model_at(h, "temp", r["ms"], 1_800_000)
            if x:
                d.append(x["v"] - r["tmpc"])
        md = avg(d)
        if finite(md):
            means.append(md)
        reading = ("not enough matching hours" if not finite(md) else
                   f"running {abs(md):.1f} °C too {'warm' if md > 0 else 'cold'} right now" if abs(md) > 2 else
                   "close to the observations")
        rows.append({"Model": m, "Matched hours (last 6 h)": len(d),
                     "Mean model − observation (°C)": round(md, 1) if finite(md) else None, "Reading": reading})
        if finite(md) and abs(md) > 2:
            warns.append(f"{m} is running {abs(md):.1f} °C too {'warm' if md > 0 else 'cold'} right now")
    return {"rows": rows, "warns": warns, "mean": avg(means)}


def ens_day_stats(ens, d):
    if not ens or not ens.get("members"):
        return None
    idx = [i for i, t in enumerate(ens["time"]) if local_day(t) == d]
    pidx = [i for i, t in enumerate(ens["time"]) if pr_day_of(t, True) == d]
    if not idx:
        return None
    per = [[], [], [], []]
    wet = npr = 0
    for m in ens["members"]:
        T = [m["temp"][i] for i in idx if i < len(m["temp"]) and finite(m["temp"][i])]
        P = [m["pr"][i] for i in pidx if i < len(m["pr"]) and finite(m["pr"][i])]
        W = [m["ws"][i] for i in idx if i < len(m["ws"]) and finite(m["ws"][i])]
        if len(T) >= 20:
            per[0].append(max(T))
            per[1].append(min(T))
        if len(P) >= 20:
            s = sum(P)
            per[2].append(s)
            npr += 1
            wet += s >= 1
        if len(W) >= 20:
            per[3].append(max(W))
    st = []
    for a in per:
        s = sorted(a)
        st.append({"n": len(s), "min": s[0] if s else None, "p10": quant(s, .1), "med": quant(s, .5),
                   "p90": quant(s, .9), "max": s[-1] if s else None})
    return {"system": ens["system"], "members": len(ens["members"]), "st": st,
            "wet": 100 * wet / npr if npr else None}


def night(models, target):
    """Mean cloud and wind of the first model with cloud data, 00–06 h of the target day."""
    for m, mod in models.items():
        h = mod.get("hourly") or {}
        if h.get("cloud") and any(finite(x) for x in h["cloud"]):
            idx = [i for i, t in enumerate(h["time"]) if local_day(t) == target and int(iso_local(t)[11:13]) <= 6]
            cl = avg([h["cloud"][i] for i in idx if i < len(h["cloud"])])
            ws = avg([h["ws"][i] for i in idx if i < len(h["ws"])])
            if finite(cl) and finite(ws):
                return {"model": m, "cloud": cl, "wind": ws}
            return None
    return None


def pressure_tendency(obs):
    if not obs:
        return None
    reps = [{"ms": r["ms"], "q": parse_metar_fields(r["metar"])["qnh"]} for r in obs["reports"] if r["metar"]]
    reps = [r for r in reps if finite(r["q"])]
    if len(reps) < 2:
        return None
    last = reps[-1]
    old = [r for r in reps if r["ms"] <= last["ms"] - 6 * HOUR]
    if not old:
        return None
    o = old[-1]
    return {"now": last["q"], "then": o["q"], "d": last["q"] - o["q"], "hours": round((last["ms"] - o["ms"]) / HOUR)}


def signals(obs, models, target, raw):
    flags = []
    g = metar_dew_gap(raw)
    f = parse_metar_fields(raw) if raw else {}
    if g:
        gap = g[0] - g[1]
        flags.append(f"Temperature–dew point gap now: {gap} °C → " +
                     ("close to saturation" if gap <= 2 else "dry air" if gap >= 10 else "moderately moist air"))
    if f.get("spd") is not None:
        d = f.get("dir")
        if d is not None and 30 <= d <= 60:
            flags.append(f"Wind from {d}°: poyraz sector → cooler Black Sea air, often more cloud")
        if d is not None and 200 <= d <= 240:
            flags.append(f"Wind from {d}°: lodos sector → warmer air, strong gusts possible")
        if g and g[0] - g[1] <= 2 and f["spd"] <= 5:
            flags.append("Small gap and light wind: fog or mist possible tonight if the sky clears")
        if f["spd"] >= 15:
            flags.append(f"Strong wind now ({f['spd']} kt): if it stays windy tonight, the air keeps mixing and the night may not cool much")
    n = night(models, target)
    if n:
        verdict = ("clear and calm: expect stronger cooling than the model" if n["cloud"] < 30 and n["wind"] < 6 else
                   "cloudy or windy: little night cooling" if n["cloud"] > 70 or n["wind"] > 12 else "no strong signal")
        flags.append(f"{n['model']} for tomorrow 00–06 h: mean cloud {n['cloud']:.0f}%, mean wind {n['wind']:.0f} kt → {verdict}")
    return flags


def clim_for(clim, d):
    if not clim:
        return None
    v = clim.get(d[5:])
    return {"v": v, "src": "ERA5 1991–2020 ±7 days (model-based; precipitation = 00–24 daily mean)"} if v else None


def extreme_times(obs, now=None):
    if not obs:
        return []
    today = local_day(now or now_ms())
    out = []
    for i in range(7, -1, -1):
        d = shift(today, -i)
        reps = [r for r in obs["reports"] if local_day(r["ms"]) == d]
        if len(reps) < 20:
            continue

        def pick(key, better):
            b = None
            for r in reps:
                if finite(r[key]) and (b is None or better(r[key], b[key])):
                    b = r
            return b
        od = obs["daily"].get(d)
        out.append({"d": d, "done": bool(od and od["complete"]), "tx": pick("tmpc", lambda a, b: a > b),
                    "tn": pick("tmpc", lambda a, b: a < b), "w": pick("sknt", lambda a, b: a > b),
                    "pr": od["prHours"] if od else []})
    return out
