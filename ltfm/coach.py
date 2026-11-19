"""Forecast coach: READ → CHECK → DECIDE. Shows evidence, never the answer."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .metar import compass, local_wind, metar_dew_gap, parse_metar_fields
from .observations import (avg, coach_blocks, ens_day_stats, latest_report, live_bias, model_at, night, obs_val,
                           pressure_tendency, reality_check)
from .timeutil import HOUR, day_start, finite, iso_local, local_day, now_ms, pretty_date, shift

COACH_TOL = [0.5, 0.5, 0.25, 1.5]
COACH_TXT = [
    {"name": "Maximum temperature", "u": "°C", "what": "the highest temperature of the whole day",
     "read": "Find the **highest number** in the row of your model.",
     "blocks": "highest temperature in each 3-hour block",
     "miss": "Not yet. Look along the whole row of your model and take the largest number (usually 12–15 or 15–18 h)."},
    {"name": "Minimum temperature", "u": "°C", "what": "the lowest temperature of the whole day",
     "read": "Find the **lowest number** in the row of your model.",
     "blocks": "lowest temperature in each 3-hour block",
     "miss": "Not yet. Take the smallest number of the row. Usually early morning (03–06 or 06–09 h), sometimes late evening."},
    {"name": "Precipitation (09:00 → 09:00)", "u": "mm",
     "what": "the total rain from 09:00 on your forecast day to 09:00 the next morning (MGM’s 24-hour period)",
     "read": "**Add up all eight numbers** in the row of your model (09–12 + 12–15 + … + 06–09). All 0.0 → 0.0 mm.",
     "blocks": "rain in each 3-hour block (mm), from 09:00 on your forecast day to 09:00 the next morning",
     "miss": "Not yet. Add all eight numbers of the row together."},
    {"name": "Maximum sustained wind", "u": "kt", "what": "the strongest sustained (mean) wind of the day in knots, not a gust",
     "read": "Find the **highest number** in the row of your model.",
     "blocks": "strongest sustained wind in each 3-hour block (kt)",
     "miss": "Not yet. Take the largest number of the row. These are sustained winds already: do not use gusts."},
]
BLOCK_COLS = [["00–03", "03–06", "06–09", "09–12", "12–15", "15–18", "18–21", "21–24"]] * 2 + \
             [["09–12", "12–15", "15–18", "18–21", "21–24", "00–03", "03–06", "06–09"]] + \
             [["00–03", "03–06", "06–09", "09–12", "12–15", "15–18", "18–21", "21–24"]]
READ_HOW = [
    "Highest temperature of the day, usually 13:00–16:00 Istanbul (mid-afternoon).",
    "Lowest temperature of the day, usually around sunrise (05:00–08:00 in this season). Also check 21:00–24:00.",
    "Add up the rain from 09:00 tomorrow to 09:00 the day after (MGM’s 24-hour period). No rain anywhere → 0.0 mm.",
    "Highest sustained wind of the day in kt. Use the Wind row, not the Gust row.",
]
MEASURE = [
    ("2 m temperature in a ventilated screen, reported in every METAR (every 30 min), whole °C. `19/10` = 19 °C, dew point 10 °C.",
     "Checked against the **highest METAR temperature between 00:00 and 24:00 Istanbul time**."),
    ("The same 2 m sensor as Tmax, from the same METARs.",
     "Checked against the **lowest METAR temperature between 00:00 and 24:00 Istanbul**: the night *before* sunrise of your day counts."),
    ("Rain gauge, 1 mm = 1 litre per m². The METAR only says *that* it rains (`-RA`, `RA`, `+RA`, `SHRA`, `TSRA`, `DZ`). "
     "MGM gives 24-hour totals 06–06 UTC = 09:00–09:00 Istanbul.",
     "Your number = total from **09:00 on your day to 09:00 the next morning**. No precipitation in any METAR of those 24 h → 0.0 mm. "
     "If it rained, the MGM amount from your instructor is used; until then the day is left out, never set to 0."),
    ("Anemometer 10 m above ground; the METAR gives the **10-minute mean** (sustained) and the direction it comes FROM. "
     "`05017G28KT` = from 050° at 17 kt, gusts 28 kt.",
     "Checked against the **highest sustained speed in any METAR of the day**, in knots. Gusts do not count. 1 kt ≈ 1.85 km/h."),
]
DECIDE = [
    ["Start from the model maximum (Step 1).", "Correct for the model’s recent bias (+1 °C bias → take 1 °C off).",
     "Sun all day → the model value or a bit more. Thick cloud, rain or fog → lower.",
     "Poyraz (Black Sea) → cooler; lodos or south wind → warmer.", "Is the model already too warm or too cold today?"],
    ["Start from the model minimum (Step 1).", "Clear sky + weak wind tonight → go lower than the model.",
     "Cloud or wind tonight → little cooling: model value or higher.",
     "Fog point ≈ afternoon dew point − 2 °C: once fog forms, the temperature hardly falls (Met Office FRB).",
     "Cold air arriving (front, strong poyraz) → lower; lodos → higher."],
    ["First YES or NO: do most models show rain? Does the TAF? Is the pressure falling?",
     "If NO → 0.0 mm. Most days at LTFM are dry.", "If YES → the middle of the model totals, not the wettest one.",
     "Showers are local and predictable only a few hours ahead (Met Office).",
     "Only rain between 09:00 tomorrow and 09:00 the day after counts."],
    ["Start from the model maximum wind. Sustained wind, never gusts.", "Correct for the model’s recent bias.",
     "Tight pressure gradient, poyraz or lodos → stronger; high-pressure centre overhead → weaker.",
     "Check the TAF wind groups and today’s METAR wind.", "Night and morning are calmer; the maximum is usually in the afternoon."],
]
MAE_TIP = ("**Scoring tip:** you are scored by the size of your error (MAE). When the models disagree, the best single "
           "number is usually the **middle one** (median), not an extreme.")
CAUSES = ['More/less cloud than expected', 'A front passed', 'Wind changed direction or speed', 'Precipitation timing',
          'Fog or low cloud', 'Other']
REASONS = ['Historical model bias', 'Strong wind / enhanced mixing', 'Cloud cover limiting radiative cooling',
           'Clear skies / enhanced radiative cooling', 'Dew point / fog / low cloud effects', 'Pressure trend',
           'Approaching precipitation', 'Radar evidence', 'Satellite evidence', 'Poyraz', 'Lodos', 'Temperature trend', 'Other']


# ---------------------------------------------------------------- TAF hour by hour
def _taf_at(dd, hh, mm, now):
    n = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
    y, mo = n.year, n.month
    if int(dd) < n.day - 15:
        mo += 1
    elif int(dd) > n.day + 15:
        mo -= 1
    if mo == 13:
        y, mo = y + 1, 1
    if mo == 0:
        y, mo = y - 1, 12
    base = datetime(y, mo, 1, tzinfo=timezone.utc).timestamp() * 1000
    return int(base + (int(dd) - 1) * 86400000 + int(hh) * HOUR + int(mm or 0) * 60000)


def taf_segments(raw, now=None):
    now = now or now_ms()
    toks = str(raw or '').strip().upper().split()

    def per(s):
        m = re.fullmatch(r'(\d{2})(\d{2})/(\d{2})(\d{2})', s)
        return (_taf_at(m[1], m[2], 0, now), _taf_at(m[3], m[4], 0, now)) if m else None

    valid, segs, cur, i = None, [], None, 0
    while i < len(toks):
        t = toks[i]
        if t in ('TAF', 'AMD', 'COR', 'LTFM') or re.fullmatch(r'\d{6}Z', t):
            i += 1
            continue
        if not valid and per(t):
            valid = per(t)
            cur = {"kind": "main", "from": valid[0], "to": valid[1], "toks": []}
            i += 1
            continue
        if t in ('BECMG', 'TEMPO') or re.fullmatch(r'PROB\d{2}', t) or re.fullmatch(r'FM\d{6}', t):
            if cur:
                segs.append(cur)
            kind = 'becmg' if t == 'BECMG' else 'tempo' if t == 'TEMPO' else 'prob' if t.startswith('PROB') else 'fm'
            label = t
            if kind == 'prob' and i + 1 < len(toks) and toks[i + 1] == 'TEMPO':
                i += 1
                label += ' TEMPO'
            p = None
            if kind == 'fm':
                m = re.fullmatch(r'FM(\d{2})(\d{2})(\d{2})', t)
                p = (_taf_at(m[1], m[2], m[3], now), valid[1] if valid else None)
            elif i + 1 < len(toks) and per(toks[i + 1]):
                p = per(toks[i + 1])
                i += 1
            cur = {"kind": kind, "label": label, "from": p[0] if p else None, "to": p[1] if p else None, "toks": []}
            i += 1
            continue
        if cur:
            cur["toks"].append(t)
        i += 1
    if cur:
        segs.append(cur)
    if not valid:
        return None
    return {"valid": valid, "segs": segs}


def _elem(t):
    if re.fullmatch(r'(\d{3}|VRB)(\d{2,3})(G\d{2,3})?KT', t):
        return 'wind'
    if re.match(r'(FEW|SCT|BKN|OVC)\d{3}', t) or t in ('NSC', 'SKC'):
        return 'cloud'
    if re.fullmatch(r'\d{4}', t):
        return 'vis'
    if t == 'CAVOK':
        return 'cavok'
    if t == 'NSW' or t == 'TS' or re.fullmatch(r'([-+]|VC)?(MI|BC|PR|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|PL|GR|GS|BR|FG|HZ|FU|SQ)+', t):
        return 'wx'
    return None


def _groups(toks):
    g = {}
    for t in toks:
        e = _elem(t)
        if not e:
            continue
        if e == 'cavok':
            g.update(vis=['9999'], cloud=['NSC'], wx=['NSW'])
            continue
        g.setdefault(e, []).append(t)
    return g


def taf_hours(taf_raw, target, off_h=0):
    P = taf_segments(taf_raw)
    if not P:
        return None
    d0 = day_start(target) + off_h * HOUR
    hrs = []
    for h in range(24):
        t = d0 + h * HOUR + 1_800_000
        if t < P["valid"][0] or t >= P["valid"][1]:
            hrs.append(None)
            continue
        st = {}
        for s in P["segs"]:
            if s["kind"] == 'main' or (s["kind"] == 'becmg' and s["to"] and t >= s["to"]) or (s["kind"] == 'fm' and s["from"] and t >= s["from"]):
                st.update(_groups(s["toks"]))
        tmp = [{"label": s["label"], "g": _groups(s["toks"])} for s in P["segs"]
               if s["kind"] in ('tempo', 'prob') and s["from"] and s["to"] and s["from"] <= t < s["to"]]
        hrs.append({"st": st, "tmp": tmp})
    return {"P": P, "hrs": hrs, "ends": P["valid"][1]}


def _spd(w):
    m = re.match(r'(?:\d{3}|VRB)(\d{2,3})', w)
    return int(m[1]) if m else None


def taf_evidence(taf_raw, k, target):
    T = taf_hours(taf_raw, target, 9 if k == 2 else 0) if taf_raw else None
    if not T:
        return ("What does the official TAF say for your day?", "TAF not available here.", "Use the models and the observations.")
    hrs = [x for x in T["hrs"] if x]
    cov = len(hrs)
    winds = [_spd(x["st"]["wind"][0]) for x in hrs if x["st"].get("wind")]
    tw = [_spd(w) for x in hrs for tp in x["tmp"] for w in tp["g"].get("wind", [])]
    wetre = re.compile(r'(DZ|RA|SN|TS|GR|PL)')
    wet = any(any(wetre.search(w) for w in x["st"].get("wx", [])) for x in hrs)
    twet = any(any(wetre.search(w) for tp in x["tmp"] for w in tp["g"].get("wx", [])) for x in hrs)
    if k == 3:
        tip = (f"Prevailing sustained wind during your day: highest {max(winds)} kt" + (f" (temporarily up to {max(tw)} kt)" if tw else "")
               + f", for the {cov} hours the TAF covers.") if winds else "No wind information for your day."
    elif k == 2:
        tip = ("Precipitation is the prevailing weather: give an amount, not 0." if wet else
               "Precipitation only in TEMPO/PROB groups: a small amount or 0, explain your choice." if twet else
               "No precipitation in the TAF for your day: a sign for 0.0 mm.")
    else:
        rng = (0, 9) if k == 1 else (9, 18)
        sub = [x for x in T["hrs"][rng[0]:rng[1]] if x]
        if not sub:
            tip = "The TAF does not cover these hours."
        else:
            ws = [_spd(x["st"]["wind"][0]) for x in sub if x["st"].get("wind")]
            thick = any(re.match(r'(BKN|OVC)', c) for x in sub for c in x["st"].get("cloud", []))
            wt = any(re.search(r'(RA|DZ|SN|TS)', w) for x in sub for w in x["st"].get("wx", []))
            mw = min(ws) if ws else None
            if k == 1:
                tip = (f"TAF 00:00–09:00: wind at least {mw if mw is not None else '?'} kt" + (", broken/overcast cloud" if thick else ", little cloud") + ". "
                       + ("Wind or cloud at night → little cooling: the minimum stays near the model or higher." if (mw or 0) >= 10 or thick
                          else "Light wind and little cloud → strong cooling possible: the minimum can be lower than the model."))
            else:
                tip = ("TAF 09:00–18:00: " + ("broken/overcast cloud" if thick else "little cloud") + (" and precipitation" if wt else "") + ". "
                       + ("Thick cloud or rain → less warming: the maximum can stay below the model." if thick or wt
                          else "Mostly sunny → the maximum can reach the model value."))
    return ("What does the official TAF say for your day?",
            f"The TAF covers **{cov} of the 24 hours** of your {'precipitation day (09:00 → 09:00)' if k == 2 else 'day'} "
            f"(it ends {iso_local(T['ends'])[5:16].replace('T', ' ')} Istanbul).",
            tip + " Read it, do not copy it: your number must come from your own reasoning.")


# ---------------------------------------------------------------- evidence list
def evidence(live, k, target, m, persist_vals, now=None):
    now = now or now_ms()
    L = []
    models, obs = live["models"], live["obs"]
    h = models[m]["hourly"]
    rc = reality_check(obs, models, now)
    b = live_bias(live["prev"], obs, m, k, now)
    lr = latest_report(obs)
    u = COACH_TXT[k]["u"]

    def day_mean(key, h0, h1):
        v = [h[key][i] for i, t in enumerate(h["time"]) if h.get(key) and i < len(h[key]) and local_day(t) == target
             and h0 <= int(iso_local(t)[11:13]) <= h1 and finite(h[key][i])]
        return avg(v)

    if k < 2 and finite(rc["mean"]):
        md = rc["mean"]
        L.append(("Are the models right today?",
                  f"Over the last 6 hours the models were on average **{md:+.1f} °C** compared with the METAR "
                  f"({'close to reality' if abs(md) < 1 else 'too warm' if md > 0 else 'too cold'}).",
                  ("If this continues, a slightly lower value is reasonable." if md > 0 else "If this continues, a slightly higher value is reasonable.")
                  if abs(md) >= 1 else "No correction needed from this."))
    if k == 0:
        c = day_mean("cloud", 9, 16)
        cls = '' if not finite(c) else 'mostly sunny' if c < 25 else 'partly cloudy' if c < 60 else 'mostly cloudy' if c < 85 else 'overcast'
        L.append(("Sun or cloud during the day?",
                  f"{m} cloud cover 09–16 h tomorrow: **{f'{c:.0f}% = {cls}' if finite(c) else 'not available'}**.",
                  "No cloud data." if not finite(c) else
                  "Mostly sunny → the maximum can reach the model value or a bit more." if c < 25 else
                  "Partly cloudy → no strong correction from cloud alone." if c < 60 else
                  "Cloudy → less warming: the maximum can stay a little below the model."))
    if k == 1:
        n = night(models, target)
        if n:
            L.append(("Clear and calm tonight?", f"Tomorrow 00–06 h ({n['model']}): cloud **{n['cloud']:.0f}%**, wind **{n['wind']:.0f} kt**.",
                      "Clear and calm → strong cooling: the minimum is often lower than the model." if n["cloud"] < 30 and n["wind"] < 6 else
                      "Cloudy or windy → little cooling: the minimum stays close to the model or higher." if n["cloud"] > 70 or n["wind"] > 12 else
                      "Neither very clear nor very cloudy: no strong correction from this."))
        if lr and lr["metar"]:
            g = metar_dew_gap(lr["metar"])
            if g:
                aft = [metar_dew_gap(r["metar"]) for r in obs["reports"] if r["metar"] and local_day(r["ms"]) == local_day(now)
                       and 12 <= int(iso_local(r["ms"])[11:13]) <= 15]
                aft = [x for x in aft if x]
                td = round(avg([x[1] for x in aft])) if aft else None
                L.append(("How moist is the air? Where could fog stop the cooling?",
                          f"Latest METAR: temperature {g[0]} °C, dew point {g[1]} °C (gap {g[0] - g[1]} °C)." +
                          (f" Afternoon dew point today ≈ **{td} °C** → first guess of the fog point ≈ **{td - 2} °C**." if td is not None else ""),
                          f"If your minimum would fall below about {td - 2} °C, fog may form first and stop the cooling." if td is not None else
                          ("Small gap: fog or low cloud may form and stop the cooling." if g[0] - g[1] <= 3 else "Large gap: dry air cools easily on a clear night.")))
    if k == 2:
        names = list(models)
        blocks = {x: coach_blocks(models, x, 2, target) for x in names}
        allm = [x for x in names if blocks[x]]
        wet = [x for x in allm if blocks[x]["day"] >= 0.1]
        es = ens_day_stats(live["ens"], target)
        L.append(("Do the models agree on rain?",
                  f"**{len(wet)} of {len(allm)}** models show rain from 09:00 tomorrow to 09:00 the day after" +
                  (f"; {es['wet']:.0f}% of the ensemble members give at least 1 mm" if es and finite(es["wet"]) else "") + ".",
                  "No model shows rain → 0.0 mm is the safe choice." if not wet else
                  "All models show rain → give an amount, not 0." if len(wet) == len(allm) else
                  "Models disagree → choose a small amount or 0 and explain why."))
    if k == 3:
        rec = [r for r in obs["reports"] if r["ms"] > now - 6 * HOUR and finite(r["sknt"])] if obs else []
        d = [mv["v"] - r["sknt"] for r in rec if (mv := model_at(h, "ws", r["ms"], 1_800_000))]
        if len(d) >= 3:
            md = avg(d)
            L.append(("Is the model right about the wind today?",
                      f"Over the last 6 hours {m} was on average **{md:+.0f} kt** compared with the METAR wind ({len(d)} reports).",
                      "The model is too weak right now: a higher value can be reasonable." if md <= -3 else
                      "The model is too strong right now: a lower value can be reasonable." if md >= 3 else
                      "Close to reality: no correction needed from this."))
        L.append(("Why is the observed maximum often a bit above the model?",
                  "The observation is the highest of about 48 half-hourly METARs; a model gives smooth hourly grid-box values.",
                  "So the observed maximum tends to be a little higher than the model’s highest value."))
        if lr and lr["metar"]:
            f = parse_metar_fields(lr["metar"])
            if f["spd"] is not None:
                dd = f["dir"]
                L.append(("What is the wind doing now?",
                          f"Latest METAR: from **{f'{dd:03d}° ({compass(dd)})' if dd is not None else 'variable'}** at **{f['spd']} kt**"
                          + (f" ({local_wind(dd)})" if dd is not None and local_wind(dd) else "") + ".",
                          "Poyraz and lodos days at LTFM are often windy all day."))
    if k != 2:
        if b and b["n"]:
            L.append(("Is this model usually too high or too low?",
                      f"{m} was on average **{b['bias']:+.1f} {u}** wrong on the last {b['n']} days (+ = too high).",
                      "Correct for it: bias +1 → take 1 off; bias −1 → add 1." if abs(b["bias"]) >= 0.5 else "The bias is small: no correction needed."))
        else:
            L.append(("Is this model usually too high or too low?", "Not known (needs the past model runs).", "Skip this step for now."))
    p = pt = pressure_tendency(obs)
    if pt and k in (0, 2, 3):
        L.append(("Is the pressure rising or falling?", f"QNH {p['then']} → **{p['now']} hPa** in the last {p['hours']} h ({p['d']:+d} hPa).",
                  "Falling fast: a low or front may be coming → more cloud, rain and wind possible." if p["d"] <= -3 else
                  "Rising fast: high pressure building → drier; wind can be strong at first, then weaker." if p["d"] >= 3 else
                  "Small change: no strong signal from the pressure."))
    pv = persist_vals[k] if persist_vals else None
    yod = obs["daily"].get(shift(local_day(now), -1)) if obs else None
    tod = obs["daily"].get(local_day(now)) if obs else None
    tv = obs_val(tod, k) if k != 2 else None
    txt = (f"Yesterday’s observed value: **{pv:g} {u}** (persistence, the bar you must beat)." if finite(pv) else
           ("Precipitation day of yesterday: it rained (" + str(yod['prN']) + " METARs); the amount comes from MGM." if yod['prN'] else
            "Precipitation day of yesterday: dry, 0.0 mm.") if k == 2 and yod else "Yesterday’s value is not available.")
    if finite(tv):
        txt += f" Today so far: **{tv:g} {u}** (not final)."
    L.append(("What happened yesterday and today?", txt,
              "If the weather type stays the same, tomorrow is often close to yesterday. If it changes (front, wind shift), it is not."))
    taf = live["taf"][0]["rawTAF"] if live.get("taf") else None
    L.append(taf_evidence(taf, k, target))
    return L
