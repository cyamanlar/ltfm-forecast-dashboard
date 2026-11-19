"""LTFM Student Dashboard (Python / Streamlit version)

Istanbul Airport (LTFM) forecast competition · MTO111E · İTÜ

Run:   streamlit run app.py      (or double-click run_dashboard.bat on Windows)

Read the evidence · make your own forecast · check your errors · beat persistence · report honestly.
Real open data only. Nothing is invented. Your records are saved in data/state.json.
"""
from __future__ import annotations

import json
import math
from datetime import date as _date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from ltfm import coach, config as C, report, selftest, sources, storage
from ltfm.metar import compass, decode_metar, decode_taf, local_wind, metar_groups, parse_metar_fields
from ltfm.observations import (avg, clim_for, closest_metar, coach_blocks, compute_persistence, dry_day,
                               ens_day_stats, extreme_times, latest_report, live_bias, model_at, obs_val,
                               reality_check, signals)
from ltfm.scoring import (clim_scores, complete, exclusion, model_scores, ontime, participation, participation_text,
                          rec_status, scores, skill_series, valid_vector)
from ltfm.timeutil import (H3, HOUR, countdown_text, day_end, day_label, deadline, finite, iso_local, iso_utc,
                           local_day, nice, now_ms, parse_ts, pr_window, pretty_date, r1, shift, stamp, to_dt_local)

LABELS, UNITS = C.LABELS, C.UNITS
st.set_page_config(page_title="LTFM Student Dashboard", page_icon="🌤️", layout="wide")
st.markdown("""<style>
.block-container{padding-top:1.2rem;max-width:1250px}
.ltfm-day{display:inline-block;width:62px;margin:2px;padding:4px 2px;text-align:center;border:1px solid #c9d6de;border-radius:6px;font-size:12px}
.ltfm-day b{display:block;font-size:14px}.ok{background:#e2f3ec}.no{background:#f9e8e2}.pend{background:#f4f5f4}
.code{font-family:Consolas,Menlo,monospace;background:#f4f7f6;border:1px solid #c9d6de;border-radius:6px;padding:6px 9px}
</style>""", unsafe_allow_html=True)


# ====================================================================== helpers
def fmt(x, d=1):
    return f"{x:.{d}f}" if finite(x) else "N/A"


def fmtm(x, d=1):
    return f"{x:.{d}f}" if finite(x) else "missing"


def sgn(x, d=1):
    return f"{x:+.{d}f}" if finite(x) else "N/A"


def dk(k):
    return 0 if k == 3 else 1


def df(rows, cols=None):
    return pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame(rows)


def show_df(rows, cols=None, empty="No records in this period."):
    if not rows:
        st.caption(empty)
        return
    st.dataframe(df(rows, cols), hide_index=True)


def fragment(fn):
    f = getattr(st, "fragment", None)
    return f(run_every=1)(fn) if f else fn


def save():
    try:
        storage.save_state(state)
    except OSError as e:
        st.error(f"Could not save to data/state.json ({e}). Download a CSV backup now (tab 5).")


S = st.session_state
if "state" not in S:
    S.state = storage.load_state()
    S.nonce = 0
state = S.state
storage.sync_calendar(state)
cal = state["calendar"]


@st.cache_data(ttl=900, show_spinner=False)
def get_live(ens_id: str, need_clim: bool, nonce: int):
    return sources.load_all(ens_id, need_clim)


@st.cache_data(ttl=3600, show_spinner=False)
def get_chart(prod, valid):
    return sources.ecmwf_chart(prod, valid)


# ====================================================================== data
ens_id = S.get("ens_id", C.ENSEMBLES[0])
with st.spinner("Loading METAR observations, TAF and model forecasts for LTFM (up to 20 s)…"):
    live = get_live(ens_id, state.get("clim") is None, S.nonce)
if live.get("clim") and not state.get("clim"):
    state["clim"] = live["clim"]
    state["climMeta"] = {"loaded": stamp(now_ms()), "source": "Open-Meteo historical API, ERA5 1991–2020, ±7-day window mean."}
    save()

NOW = now_ms()
TODAY = local_day(NOW)
YEST, TARGET = shift(TODAY, -1), shift(TODAY, 1)
models, obs, prev = live["models"], live["obs"], live["prev"]
TAF = live["taf"][0]["rawTAF"] if live.get("taf") else None
trec = storage.find_rec(state, TARGET)
REVEALED = bool(trec and trec.get("submitted"))
model_names = sorted(models, key=lambda m: ["ECMWF IFS", "ICON", "GFS", "Météo-France", "UKMO", "MET Norway"].index(m)
                     if m in ["ECMWF IFS", "ICON", "GFS", "Météo-France", "UKMO", "MET Norway"] else 99)


def pending_b():
    pb = S.get("pendingB")
    if pb and pb["date"] == YEST:
        return pb
    pb = compute_persistence(obs, YEST, NOW)
    if pb:
        S.pendingB = pb
    return pb


def persist_values():
    if trec and trec.get("b") and any(finite(x) for x in trec["b"]):
        return trec["b"]
    pb = pending_b()
    return pb["v"] if pb else [None] * 4


# ====================================================================== header
with st.sidebar:
    st.markdown("### ⟳ Data")
    if st.button("Load latest data", type="primary"):
        S.nonce += 1
        st.rerun()
    sts = list(live["status"].values())
    okn, snapn = len([s for s in sts if s["ok"]]), len([s for s in sts if s.get("snap")])
    st.caption(f"✓ {okn} of {len(sts)} sources live · {snapn} from saved real data · loaded "
               f"{nice(stamp(live['refreshedAt']))[11:16]} Istanbul")
    st.markdown("### 👤 Student")
    nm = st.text_input("Name", state["settings"].get("name", ""))
    sid = st.text_input("Student number", state["settings"].get("studentId", ""))
    if (nm, sid) != (state["settings"].get("name"), state["settings"].get("studentId")):
        state["settings"]["name"], state["settings"]["studentId"] = nm.strip(), sid.strip()
        save()
    st.markdown("### 📋 Today’s routine")
    prev_rec = sorted([r for r in state["records"] if day_end(r["date"]) < NOW], key=lambda r: r["date"])
    prev_rec = prev_rec[-1] if prev_rec else None
    checked = (not prev_rec) or (any(finite(x) for x in prev_rec["o"]) and prev_rec["date"] in state["notes"])
    st.markdown(("✅" if checked else "⬜") + " 1 · Check yesterday" +
                (f" ({prev_rec['date']})" if prev_rec and not checked else ""))
    st.markdown(("✅" if okn or snapn else "⬜") + " 2 · Read the weather")
    st.markdown(("✅" if REVEALED else "⬜") + " 3 · Forecast and submit by 21:00")
    st.markdown(("✅" if state.get("lastBackup") == TODAY else "⬜") + " 4 · Save a CSV backup")

st.caption(f"MTO111E · Istanbul Airport · LTFM · {C.LAT} N, {C.LON} E")
st.title("Istanbul Airport (LTFM) forecast dashboard")
st.caption(f"LTFM Student Dashboard · {state['settings'].get('name') or 'student'}"
           f"{', ' + state['settings']['studentId'] if state['settings'].get('studentId') else ''} · "
           "İTÜ İklim Bilimi ve Meteoroloji Mühendisliği Bölümü · "
           "Read the evidence · make your own forecast · check your errors · beat persistence · report honestly")

if not okn and snapn:
    st.warning("Offline view: no live source answered, so you see **real data saved earlier** (labelled with date and time).")
elif not okn:
    st.error("No live source answered (no internet connection?). Nothing is invented; your saved records and scores still work.")
else:
    st.success("**Live real data** · every number shows its source (observation = METAR, model forecast, or model analysis). Nothing is invented.")


# ---------------------------------------------------------------- today's job
@fragment
def clock():
    n = now_ms()
    left = deadline(shift(local_day(n), 1)) - n
    c1, c2 = st.columns([3, 1])
    c1.markdown(f"#### Today’s job · forecast LTFM for **{pretty_date(shift(local_day(n), 1))}**")
    c2.metric("Deadline 21:00:00 today", countdown_text(left))
    st.caption(f"Istanbul now: {iso_local(n).replace('T', ' ')} (UTC+3, independent of this computer’s time zone).")


with st.container(border=True):
    clock()
    pv = persist_values()
    rows = []
    for k in range(4):
        y = fmt(pv[k], dk(k)) if finite(pv[k]) else ("available after 09:00 today" if k == 2 and NOW < parse_ts(TODAY + "T09:00:00+03:00")
                                                       else "rain reported · amount from MGM" if k == 2 else "—")
        if REVEALED:
            vals = [models[m]["daily"].get(TARGET, [None] * 4)[k] for m in models]
            vals = [v for v in vals if finite(v)]
            mc = f"{fmt(avg(vals), dk(k))} ({fmt(min(vals), dk(k))}–{fmt(max(vals), dk(k))}, {len(vals)} models)" if vals else "—"
        else:
            mc = coach.READ_HOW[k]
        mine = fmt(trec["f"][k], dk(k)) if trec and finite(trec["f"][k]) else "—"
        rows.append([LABELS[k], y, mc, mine])
    show_df(rows, ["Value", f"Yesterday {day_label(YEST)} · observed · the bar to beat",
                   "Models said (mean, range)" if REVEALED else "How to find it in the model data", f"My forecast {day_label(TARGET)}"])
    st.markdown(("✅ **Submitted** " + nice(trec["submitted"])[11:16] + f" ({rec_status(trec)})") if REVEALED else
                ("📝 Draft only · not submitted" if trec else "⚠️ Not submitted yet"))

with st.expander("Competition rules: ≥ 70% participation · submit by 21:00:00 · check yesterday every day (tap to read all rules)"):
    st.markdown("""
1. **What you forecast.** For the valid date (tomorrow) at LTFM: **Tmax** and **Tmin** in °C and the **maximum sustained wind** in kt for 00:00–24:00 Istanbul; **precipitation** in mm for **09:00 on the valid date → 09:00 the next morning** (06–06 UTC, MGM’s rain-gauge period).
2. **Deadline.** One forecast per valid date, by **21:00:00 Istanbul time** the day before. 21:00:01 is late. After Submit the numbers are locked; a second submission is refused.
3. **Complete forecast.** All four numbers plus your reasoning. Precipitation is an amount in mm, never a probability. Wind is sustained (10-min mean), never a gust.
4. **Observations.** Tmax/Tmin = highest/lowest LTFM METAR temperature of the Istanbul day; wind = highest sustained speed in any METAR. Precipitation = MGM 06–06 UTC total; no precipitation in any METAR 09→09 → 0.0 mm. A model analysis is never an observation.
5. **Persistence (bar to beat).** Yesterday’s observed values (precipitation: 09:00 yesterday → 09:00 today), saved with date and capture time.
6. **Scores.** Per variable: error, MAE, bias, **skill % = 100 × (1 − my MAE ÷ persistence MAE)**, wins/ties/losses. The four variables are never added together.
7. **Late, missing or incomplete** forecasts count as not submitted and are left out of the scores. A missing observation is left out, never set to zero.
8. **Participation.** Required = 70% of the scheduled days (5 October 2026 → end date), rounded up.
9. **Before you submit** you see hourly model data, observations, the TAF and the coach, but not tomorrow’s ready daily model numbers.
10. **Your own work.** Forecasts, reasoning and reports are individual. Keep your AI-use log (tab 5).
11. **Backup and report.** Save a CSV backup after each forecast (tab 5). At the end print the report as **MTO111E_StudentID_Forecast_Report.pdf**.
""")

# ---------------------------------------------------------------- glance
g1, g2, g3, g4 = st.columns(4)
with g1.container(border=True):
    od = obs["daily"].get(YEST) if obs else None
    st.markdown(f"**Yesterday · {day_label(YEST)}**  \n<small>LTFM METAR (observation)"
                + (f" · {od['n']} reports" if od else "") + "</small>", unsafe_allow_html=True)
    if od:
        a, b = st.columns(2)
        a.metric("Tmax", fmt(od["tmax"], 0) + " °C")
        b.metric("Tmin", fmt(od["tmin"], 0) + " °C")
        a.metric("Max sustained wind", fmt(od["wind"], 0) + " kt")
        b.metric("Max gust", fmt(od["gust"], 0) + " kt" if finite(od["gust"]) else "none")
        st.caption("Precipitation day 09→09: " + ("not finished yet." if not od["prComplete"] else
                   f"precipitation in {od['prN']} of {od['pn']} METARs ({', '.join(od['prHours'])} h); amount from MGM." if od["prN"]
                   else f"no precipitation in {od['pn']} METARs → 0.0 mm."))
    else:
        st.caption("Not available yet.")
with g2.container(border=True):
    td = obs["daily"].get(TODAY) if obs else None
    st.markdown(f"**Today so far · {day_label(TODAY)}**")
    if td:
        a, b = st.columns(2)
        a.metric("Tmax so far", fmt(td["tmax"], 0) + " °C")
        b.metric("Tmin so far", fmt(td["tmin"], 0) + " °C")
        a.metric("Max sustained wind", fmt(td["wind"], 0) + " kt")
        b.metric("Reports", td["n"])
        lr = latest_report(obs)
        if lr and lr["metar"]:
            st.code(lr["metar"].split(" RMK")[0], language=None)
    else:
        st.caption("Not available yet.")
with g3.container(border=True):
    st.markdown(f"**Tomorrow · {day_label(TARGET)} · your forecast**")
    if not REVEALED:
        st.caption("You make this forecast yourself. The models are evidence, not the answer. "
                   "After you submit, this card shows what the models said.")
    else:
        ms = [m for m in models if any(finite(x) for x in models[m]["daily"].get(TARGET, []))]
        col = lambda k: [v for v in (models[m]["daily"][TARGET][k] for m in ms) if finite(v)]
        a, b = st.columns(2)
        a.metric("Tmax (model mean)", fmt(avg(col(0))) + " °C")
        b.metric("Tmin (model mean)", fmt(avg(col(1))) + " °C")
        a.metric("Precipitation", fmt(avg(col(2))) + " mm")
        b.metric("Max wind", fmt(avg(col(3)), 0) + " kt")
        st.caption("Your forecast: " + " / ".join(fmt(v, dk(k)) for k, v in enumerate(trec["f"])))
with g4.container(border=True):
    st.markdown("**Official TAF · LTFM**  \n<small>aviation forecast, not an observation</small>", unsafe_allow_html=True)
    if TAF:
        st.code(TAF, language=None)
        for head, per, txt in decode_taf(TAF):
            st.markdown(f"- **{head}**{' · ' + per if per else ''}: {txt}")
    else:
        st.caption("Not available. Open the [AWC page](https://aviationweather.gov/data/metar/?id=LTFM&hours=24).")

tab_err, tab_read, tab_fc, tab_prog, tab_rep = st.tabs(
    ["1 · Check yesterday", "2 · Read weather", "3 · Make a forecast", "4 · Review progress", "5 · Save your report"])


# ====================================================================== charts
def fig_lines(series, title, ytitle, x0=None, x1=None, now_x=True, floor0=False, height=300, bars=False):
    fig = go.Figure()
    for s in series:
        if s.get("dots"):
            fig.add_trace(go.Scatter(x=s["x"], y=s["y"], mode="markers", name=s["name"],
                                     marker=dict(color=s.get("color", "#111"), size=s.get("size", 6))))
        elif bars:
            fig.add_trace(go.Bar(x=s["x"], y=s["y"], name=s["name"], marker_color=s.get("color"), opacity=.75))
        else:
            fig.add_trace(go.Scatter(x=s["x"], y=s["y"], mode="lines", name=s["name"],
                                     line=dict(color=s.get("color"), width=s.get("width", 2), dash=s.get("dash")),
                                     opacity=s.get("opacity", 1), showlegend=s.get("legend", True)))
    if now_x:
        fig.add_vline(x=to_dt_local(now_ms()), line_dash="dot", line_color="#b44918")
    fig.update_layout(title=title, height=height, margin=dict(l=40, r=10, t=40, b=30), yaxis_title=ytitle,
                      legend=dict(orientation="h", y=-0.2), barmode="group")
    if x0 is not None:
        fig.update_xaxes(range=[to_dt_local(x0), to_dt_local(x1)])
    if floor0:
        fig.update_yaxes(rangemode="tozero")
    return fig


def win(m, key, x0, x1):
    h = models[m]["hourly"]
    pts = [(t, h[key][i]) for i, t in enumerate(h["time"]) if h.get(key) and i < len(h[key]) and x0 <= t <= x1]
    return [to_dt_local(t) for t, _ in pts], [v for _, v in pts]


def metar_pts(x0, x1, key):
    if not obs:
        return [], []
    r = [(x["ms"], parse_metar_fields(x["metar"])[key]) for x in obs["reports"] if x["metar"] and x0 <= x["ms"] <= x1]
    r = [p for p in r if finite(p[1])]
    return [to_dt_local(t) for t, _ in r], [v for _, v in r]


def target_band(fig):
    fig.add_vrect(x0=to_dt_local(parse_ts(TARGET + "T00:00:00+03:00")), x1=to_dt_local(parse_ts(TARGET + "T23:59:59+03:00")),
                  fillcolor="#f7c948", opacity=0.10, line_width=0, annotation_text="your forecast day", annotation_position="top left")
    return fig


# ====================================================================== TAB 2 · READ WEATHER
with tab_read:
    st.subheader("Read the weather: observations first, models second")
    st.markdown("An **observation** tells you what was measured. A **model forecast** estimates what may happen. "
                "A **model analysis** is a model’s estimate of the past, not a measurement.")
    with st.expander(f"Data sources: {okn} live, {snapn} from saved data, {len(sts) - okn - snapn} not reachable (details)", expanded=okn < len(sts)):
        show_df([[s["label"], ("✓ " + s["http"] + " OK") if s["ok"] else ("saved real data" if s.get("snap") else "not reachable"),
                  "—" if s["ok"] else s["error"], s["time"] if s["ok"] else nice(s.get("cachedAt")) if s.get("snap") else "—", s["url"]]
                 for s in sts], ["Source", "Status", "Note", "Updated", "Address"])

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("#### Latest LTFM observation (METAR)")
        lr = latest_report(obs)
        if lr and lr["metar"]:
            st.caption(f"Observed {nice(stamp(lr['ms']))} ({round((NOW - lr['ms']) / 60000)} min ago) · Iowa Environmental Mesonet archive")
            st.code(lr["metar"], language=None)
            show_df(decode_metar(lr["metar"]), ["Group", "Meaning"])
        else:
            st.caption("No real LTFM METAR is available yet (see source A). No example report is shown instead.")
        with st.expander("Decode any METAR yourself"):
            mi = st.text_input("Paste a METAR", key="metar_in")
            if mi.strip():
                show_df(decode_metar(mi), ["Group", "Meaning"])
    with c2.container(border=True):
        st.markdown("#### Physical rules of thumb · correct the models")
        fl = signals(obs, models, TARGET, lr["metar"] if lr else "")
        if fl:
            st.info("\n".join("- " + x for x in fl))
        show_df([["Clear sky and light wind at night", "Strong cooling: the minimum can be lower than the model"],
                 ["Thick cloud all day", "Small day–night difference"],
                 ["Rain or thick cloud earlier/stronger than the model", "Maximum lower than the model"],
                 ["Strong wind at night", "Little cooling: minimum not very low"],
                 ["Small T−Td gap + light wind + clear night", "Fog and low visibility possible"],
                 ["Poyraz (NE, about 030–060°)", "Cooler Black Sea air, often more cloud"],
                 ["Lodos (SW, about 200–240°)", "Warmer air, strong gusts possible"]], ["If you see…", "…then expect"])

    # ---------------- multi-model hourly
    with st.container(border=True):
        st.markdown("#### LTFM · models hour by hour (Istanbul time) · black dots = METAR")
        if not model_names:
            st.caption("No model data yet. Press “Load latest data”.")
        else:
            pick = st.multiselect("Models", model_names, default=model_names, key="mm_pick")
            x0 = (NOW // HOUR) * HOUR - 24 * HOUR
            x1 = parse_ts(shift(TARGET, 1) + "T09:00:00+03:00")
            col = lambda m: C.MODEL_COLORS.get(m, "#333")
            for key, mkey, title, unit, floor in [("temp", "T", "Temperature 2 m", "°C", False), ("ws", "spd", "Sustained wind 10 m (not gusts)", "kt", True),
                                                  ("cloud", "cloud", "Cloud cover (METAR: FEW 25, SCT 50, BKN 75, OVC 100)", "%", True),
                                                  ("p", "qnh", "Sea-level pressure (METAR: QNH)", "hPa", False)]:
                ser = [{"name": m, "color": col(m), "x": win(m, key, x0, x1)[0], "y": win(m, key, x0, x1)[1]} for m in pick]
                mx, my = metar_pts(x0, NOW, mkey)
                ser.append({"name": "METAR", "dots": True, "x": mx, "y": my})
                st.plotly_chart(target_band(fig_lines(ser, title, unit, x0, x1, floor0=floor)), key="mm_" + key)
            ser = [{"name": m, "color": col(m), "x": win(m, "pr", x0, x1)[0], "y": win(m, "pr", x0, x1)[1]} for m in pick]
            st.plotly_chart(target_band(fig_lines(ser, "Precipitation per hour", "mm/h", x0, x1, floor0=True, bars=True)), key="mm_pr")
            st.caption("Where the lines lie close together the forecast is more certain; where they fan out, find out why before you choose a number. "
                       "The yellow band is your forecast day (00–24 h). Precipitation counts from 09:00 on your day to 09:00 the next morning.")

            m1 = st.selectbox("Forecast table for one model (3-hourly, like Windy)", model_names, key="wx_model")
            h = models[m1]["hourly"]
            idx = {t: i for i, t in enumerate(h["time"])}
            rows = []
            t = (NOW // H3) * H3
            while t <= x1:
                i = idx.get(t)
                if i is not None:
                    v = lambda a: h[a][i] if h.get(a) and i < len(h[a]) and finite(h[a][i]) else None
                    pre = h.get("prPrecedes", True)
                    rain = [h["pr"][j] for kk in range(3) if (j := idx.get(t - kk * HOUR if pre else t + kk * HOUR)) is not None
                            and j < len(h["pr"]) and finite(h["pr"][j])]
                    wd = v("wd")
                    rows.append([iso_local(t)[5:16].replace("T", " "), fmt(v("temp"), 0), fmt(v("td"), 0),
                                 fmt(sum(rain)) if rain else "–", fmt(v("cloud"), 0), fmt(v("ws"), 0), fmt(v("gust"), 0),
                                 (f"{compass(wd)} {local_wind(wd)}".strip() if wd is not None else ""), fmt(v("p"), 0)])
                t += H3
            show_df(rows, ["Istanbul time", "Temp °C", "Dew pt °C", "Rain 3 h mm", "Cloud %", "Wind kt", "Gust kt", "From", "Press. hPa"])
            st.caption(f"{m1} · rain = total of the 3 hours · “From” = where the wind comes FROM, as in METAR (NE = poyraz sector).")

    # ---------------- reality check
    with st.container(border=True):
        st.markdown("#### Reality check · the METAR closest to now against every model")
        cm = closest_metar(obs, NOW)
        if cm and model_names:
            f = parse_metar_fields(cm["metar"])
            st.caption(f"Closest METAR: {iso_local(cm['ms'])[11:16]} Istanbul ({iso_utc(cm['ms'])[11:16]} UTC). Model value at the nearest hour (within 1 h); "
                       "in brackets model − METAR. ❌ = far off (T/Td > 2 °C, wind > 5 kt, direction > 45°, cloud > 50%, pressure > 2 hPa).")
            rows = []
            for lab, key, ov, lim in [("Temperature (°C)", "temp", f["T"], 2), ("Dew point (°C)", "td", f["Td"], 2), ("Wind speed (kt)", "ws", f["spd"], 5),
                                      ("Wind direction (°)", "wd", f["dir"], 45), ("Cloud cover (%)", "cloud", f["cloud"], 50), ("Pressure (hPa)", "p", f["qnh"], 2)]:
                row = [lab, "—" if ov is None else ov]
                for n in model_names:
                    r = model_at(models[n]["hourly"], key, cm["ms"])
                    if not r:
                        row.append("N/A")
                        continue
                    if ov is None:
                        row.append(fmt(r["v"], 0))
                        continue
                    d = ((r["v"] - ov) % 360 if key == "wd" else r["v"] - ov)
                    if key == "wd":
                        d = min(d, 360 - d)
                    row.append(f"{'❌' if abs(d) > lim else '✓'} {fmt(r['v'], 0 if key in ('wd', 'cloud') else 1)} ({sgn(d, 0 if key == 'wd' else 1)})")
                rows.append(row)
            show_df(rows, [f"At {iso_local(cm['ms'])[11:16]}", "METAR"] + model_names)
        rc = reality_check(obs, models, NOW)
        if rc["rows"]:
            for w in rc["warns"]:
                st.warning(w + ". Trust it less for tonight unless you find a reason.")
            show_df(rc["rows"])

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("#### Last 24 hours of LTFM observations (newest first)")
        reps = [r for r in (obs["reports"] if obs else []) if r["metar"] and NOW - 24 * HOUR <= r["ms"] <= NOW + 600000][::-1]
        rows = []
        for r in reps:
            f, g = parse_metar_fields(r["metar"]), metar_groups(r["metar"])
            rows.append([iso_local(r["ms"])[11:16] + f" ({iso_utc(r['ms'])[11:16]}Z)",
                         (f"{f['dir']:03d}° {compass(f['dir'])}" if f["dir"] is not None else "VRB") + f" {f['spd']} kt" + (f" G{f['gust']}" if f["gust"] else "") if f["spd"] is not None else "missing",
                         f["T"], f["Td"], f["qnh"], ("10 km+" if (f["vis"] or 0) >= 9999 else f["vis"]), g["clouds"], g["wx"]])
        show_df(rows, ["Time (Istanbul)", "Wind", "Temp °C", "Dew pt °C", "QNH hPa", "Visibility m", "Clouds", "Weather"], "METAR not available yet.")
    with c2.container(border=True):
        st.markdown("#### How wrong was each model recently? (bias)")
        st.caption("Recent bias = the model’s forecast issued one day ahead minus the LTFM METAR value, last 7 completed days "
                   "(Open-Meteo previous-runs API). +1.0 = 1 °C too warm, so take 1 °C off. METAR has no rain amount → no precipitation bias.")
        rows = []
        for m in model_names:
            bb = [live_bias(prev, obs, m, k, NOW) for k in (0, 1, 3)]
            rows.append([m] + [f"{sgn(b['bias'])} (n={b['n']})" if b and b["n"] else "—" for b in bb])
        show_df(rows, ["Model", "Bias Tmax °C", "Bias Tmin °C", "Bias wind kt"])
        parts = []
        for k in range(4):
            v = [models[m]["daily"].get(TARGET, [None] * 4)[k] for m in model_names]
            v = [x for x in v if finite(x)]
            if len(v) < 2:
                continue
            sp = max(v) - min(v)
            verdict = ("models agree (within 1 °C): high confidence" if sp <= 1 else "models disagree (3 °C or more): uncertain" if sp >= 3
                       else "moderate agreement") if k < 2 else f"spread {sp:.1f} {UNITS[k]}"
            parts.append(f"**{LABELS[k]}**: " + (f"{min(v):.1f} – {max(v):.1f} → " if REVEALED else f"models differ by {sp:.1f} {UNITS[k]} → ") + verdict)
        if parts:
            st.info(f"**Tomorrow ({TARGET}) · model agreement**  \n" + "  \n".join(parts))

    # ---------------- ensemble
    with st.container(border=True):
        st.markdown("#### Ensemble · one model run many times with small changes")
        e1, e2 = st.columns([2, 1])
        new_ens = e1.selectbox("Ensemble system", C.ENSEMBLES, index=C.ENSEMBLES.index(ens_id) if ens_id in C.ENSEMBLES else 0)
        if new_ens != ens_id:
            S.ens_id = new_ens
            st.rerun()
        es = ens_day_stats(live["ens"], TARGET)
        if es:
            e2.metric("Members with ≥ 1 mm tomorrow", fmt(es["wet"], 0) + "%", help="Share of scenarios, not an amount.")
            ens = live["ens"]
            fig = go.Figure()
            xs = [to_dt_local(t) for t in ens["time"]]
            for i, m in enumerate(ens["members"]):
                fig.add_trace(go.Scatter(x=xs, y=m["temp"], mode="lines", line=dict(color="#9fb0ad", width=.8), opacity=.6,
                                         name="Members", showlegend=i == 0))
            mean = [avg([m["temp"][i] for m in ens["members"] if i < len(m["temp"])]) for i in range(len(xs))]
            fig.add_trace(go.Scatter(x=xs, y=mean, mode="lines", line=dict(color="#006f68", width=3), name="Ensemble mean"))
            fig.add_vline(x=to_dt_local(NOW), line_dash="dot", line_color="#b44918")
            fig.update_layout(title=f"Ensemble plume · {es['system']} · {es['members']} members · 2-m temperature", height=300,
                              margin=dict(l=40, r=10, t=40, b=30), yaxis_title="°C")
            st.plotly_chart(target_band(fig), key="ens")
            if REVEALED:
                show_df([[LABELS[k], s["n"], fmt(s["min"]), fmt(s["p10"]), fmt(s["med"]), fmt(s["p90"]), fmt(s["max"]), fmt(trec["f"][k])]
                         for k, s in enumerate(es["st"])], ["Variable", "Members", "Min", "P10", "Median", "P90", "Max", "My value"])
            else:
                st.caption("Where the grey lines stay close together the forecast is more certain. The table of tomorrow’s ensemble values appears after you submit.")
        else:
            st.caption("Ensemble not available (see the source status). A wrong identifier shows the API “reason” text.")

    # ---------------- maps
    with st.container(border=True):
        st.markdown("#### Weather maps · now, today and tomorrow")
        m1c, m2c = st.columns(2)
        with m1c:
            st.markdown("**Now · MGM surface analysis (yer kartı)**")
            st.image("https://www.mgm.gov.tr/FTPDATA/analiz/harita/yer.jpg", caption="Source: MGM. Isobars, highs, lows and fronts at the latest analysis time.")
        with m2c:
            st.markdown("**ECMWF forecast charts** (CC BY 4.0)")
            prod = st.radio("Product", ["medium-mslp-wind850", "medium-mslp-rain"], horizontal=True,
                            format_func=lambda p: "Pressure + 850 hPa wind" if "wind" in p else "Pressure + rain")
            slots = [("Latest analysis", None), ("Tomorrow 03:00", f"{TARGET}T00:00:00Z"), ("Tomorrow 09:00", f"{TARGET}T06:00:00Z"),
                     ("Tomorrow 15:00", f"{TARGET}T12:00:00Z"), ("Tomorrow 21:00", f"{TARGET}T18:00:00Z"), ("Day after 09:00", f"{shift(TARGET, 1)}T06:00:00Z")]
            sl = st.radio("Time (Istanbul)", [s[0] for s in slots], horizontal=True)
            img, page, err = get_chart(prod, dict(slots)[sl])
            if img:
                st.image(img)
            else:
                st.caption(f"The chart could not be loaded here ({err}).")
            st.markdown(f"[Open this chart on charts.ecmwf.int ↗]({page})")
        with st.expander("How to read the maps for your forecast"):
            st.markdown("""1. **Wind speed:** isobars close together over Istanbul → strong wind; far apart → light wind.
2. **Poyraz:** high pressure over the Balkans/Black Sea and lower pressure to the south → NE wind at LTFM, cool, often cloudy.
3. **Lodos:** a low over the Balkans/Black Sea and higher pressure to the south → SW wind, warm and gusty, often before a front.
4. **850 hPa wind** (about 1.5 km up) is much stronger than the 10 m wind; use only as a rough check.
5. **Rain:** find rain areas and fronts moving towards Istanbul. Is the rain inside your precipitation day (09:00 → 09:00)?""")
        if st.toggle("Show the Windy live map (needs internet)", key="windy"):
            components.iframe("https://embed.windy.com/embed2.html?lat=41.275&lon=28.752&detailLat=41.275&detailLon=28.752"
                                    "&width=650&height=450&zoom=7&level=surface&overlay=wind&product=ecmwf&menu=&message=true"
                                    "&marker=true&calendar=now&pressure=true&type=map&location=coordinates&detail=&metricWind=kt&metricTemp=%C2%B0C",
                                    height=470)

    with st.expander("More detail · observed days, model analysis, crosswind, when extremes happen, links"):
        if obs:
            rows = []
            for d in (shift(TODAY, -3), shift(TODAY, -2), YEST, TODAY):
                v = obs["daily"].get(d)
                if not v:
                    continue
                a = (live["analysis"] or {}).get(d, [None] * 4)[2]
                rows.append([d + (" (yesterday)" if d == YEST else " (today, so far)" if d == TODAY else ""), fmtm(v["tmax"], 0), fmtm(v["tmin"], 0),
                             (f"precip in {v['prN']} of {v['pn']} METARs" if v["prN"] else f"no precip in {v['pn']} METARs") + f" · analysis {fmt(a)} mm",
                             fmtm(v["wind"], 0), fmt(v["gust"], 0) if finite(v["gust"]) else "none", v["n"]])
            st.markdown("**Yesterday and today · all four parameters (observed)**")
            show_df(rows, ["Istanbul day", "Tmax °C", "Tmin °C", "Precipitation (09→09)", "Max sustained wind kt", "Max gust kt", "Reports"])
            ex = extreme_times(obs, NOW)
            st.markdown("**When did it really happen? (Istanbul time)**")
            show_df([[e["d"] + ("" if e["done"] else " (so far)"),
                      f"{e['tx']['tmpc']:g} °C · {iso_local(e['tx']['ms'])[11:16]}" if e["tx"] else "—",
                      f"{e['tn']['tmpc']:g} °C · {iso_local(e['tn']['ms'])[11:16]}" if e["tn"] else "—",
                      f"{e['w']['sknt']:g} kt · {iso_local(e['w']['ms'])[11:16]}" if e["w"] else "—",
                      ", ".join(h + "h" for h in e["pr"]) or "none"] for e in ex],
                    ["Day", "Tmax at", "Tmin at", "Max wind at", "Precipitation reported at (09→09)"])
        st.markdown("**Aviation extra · crosswind and headwind** (type a runway heading you checked in an official source)")
        a, b, c = st.columns(3)
        lf = parse_metar_fields(lr["metar"]) if lr and lr["metar"] else {}
        rwy = a.number_input("Runway heading (°)", 1, 360, 350)
        wdir = b.number_input("Wind from (°)", 0, 360, lf.get("dir") or 0)
        wspd = c.number_input("Wind speed (kt)", 0, 150, lf.get("spd") or 0)
        ang = math.radians(wdir - rwy)
        st.write(f"Crosswind **{abs(wspd * math.sin(ang)):.1f} kt** from the {'right' if wspd * math.sin(ang) >= 0 else 'left'} · "
                 f"{'headwind' if wspd * math.cos(ang) >= 0 else 'tailwind'} **{abs(wspd * math.cos(ang)):.1f} kt**")
        st.markdown("**Synoptic view and official links**")
        st.markdown("\n".join(f"- [{t}]({u})" for t, u in C.LINKS))


# ====================================================================== TAB 3 · FORECAST
with tab_fc:
    locked = REVEALED
    st.subheader("Make your own forecast for tomorrow")
    st.error(f"DAILY DEADLINE: 21:00:00 ISTANBUL TIME. Submit the forecast for {pretty_date(TARGET)} today. "
             + ("This deadline has passed: a submission now is LATE and does not count." if NOW > deadline(TARGET) else ""))
    st.caption(f"LTFM · {TARGET} 00:00–24:00 Istanbul for Tmax, Tmin and wind; precipitation 09:00 {TARGET} → 09:00 {shift(TARGET, 1)} "
               f"(06–06 UTC). Deadline {nice(stamp(deadline(TARGET)))}. Status: {rec_status(trec) if trec else 'No draft yet'}.")

    # A · first idea
    with st.container(border=True):
        st.markdown("#### A · My first idea before I look at the models (optional, recommended)")
        fi = (trec or {}).get("firstIdea") or state["firstIdeas"].get(TARGET)
        with st.form("first_idea"):
            a, b = st.columns(2)
            opts1, opts2 = ["", "Warmer", "About the same", "Colder"], ["", "Yes", "No", "Not sure"]
            trend = a.selectbox("Tomorrow compared with today", opts1, index=opts1.index(fi["trend"]) if fi else 0, disabled=locked)
            prc = b.selectbox("Precipitation tomorrow?", opts2, index=opts2.index(fi["precip"]) if fi else 0, disabled=locked)
            why = st.text_input("Why? (one sentence)", fi["why"] if fi else "", disabled=locked)
            if st.form_submit_button("Save my first idea", disabled=locked):
                if not trend or not prc or not why.strip():
                    st.error("Choose warmer/same/colder and precipitation, and write one sentence.")
                else:
                    state["firstIdeas"][TARGET] = {"trend": trend, "precip": prc, "why": why.strip(), "savedAt": stamp(now_ms())}
                    save()
                    st.success("First idea saved. Now study the models.")
        if fi:
            st.caption(f"First idea saved {nice(fi['savedAt'])}.")

    # B · persistence
    with st.container(border=True):
        st.markdown("#### B · The bar I must beat (persistence)")
        st.caption("Persistence = “tomorrow will be like yesterday”: the observed values of **yesterday**, the last full day before you submit. "
                   "Your score compares your error with the error of these values.")
        pb = None if locked else pending_b()
        bv = trec["b"] if locked else (pb["v"] if pb else [None] * 4)
        manual = st.toggle("Values wrong or missing? Edit by hand (marked as “typed” in your report)", key="manualP", disabled=locked)
        cols = st.columns(4)
        newb = []
        for k in range(4):
            newb.append(cols[k].number_input(f"Persistence {LABELS[k]}", value=float(bv[k]) if finite(bv[k]) else None, step=0.1,
                                             disabled=locked or not (manual or (k == 2 and not finite(bv[k]))), key=f"pb{k}_{YEST}"))
        b1, b2 = st.columns(2)
        if b1.button("Use model analysis for precipitation (labelled, not counted)", disabled=locked):
            a = (live["analysis"] or {}).get(YEST, [None] * 4)[2]
            if finite(a):
                S[f"pb2_{YEST}"] = float(a)
                S.prAnalysisB = True
                st.rerun()
            else:
                st.error(f"The model analysis for {YEST} is not available.")
        if not locked:
            src = list(pb["src"]) if pb else ["Typed by student"] * 4
            for k in range(4):
                if pb is None or (finite(newb[k]) and newb[k] != pb["v"][k]) or (not finite(pb["v"][k]) and finite(newb[k])):
                    src[k] = "Model analysis (Open-Meteo) — not a rain gauge" if k == 2 and S.get("prAnalysisB") else "Typed by student"
            S.curB = {"date": YEST, "v": newb, "src": src, "captured": pb["captured"] if pb and src == pb["src"] else stamp(now_ms())}
            st.caption(f"Persistence day {YEST} · " + (" | ".join(src)))
        else:
            st.caption(f"Saved persistence {trec['baselineDate']} · captured {nice(trec['baselineCaptured'])} · {trec.get('bSrc')}")
        cl = {"v": trec["c"], "src": trec.get("cSrc")} if locked and trec.get("c") else clim_for(state.get("clim"), TARGET)
        if cl:
            show_df([["Long-term average"] + [fmt(v) for v in cl["v"]] + [cl["src"]]], [f"Climatology for {TARGET}"] + LABELS + ["Source"])
        else:
            st.caption("Climatology not loaded yet (needs internet once; it is then saved).")

    # C · coach
    with st.container(border=True):
        st.markdown("#### C · Forecast coach · find your four numbers step by step")
        st.caption("In each value: **1 Read** the model, **2 Check** the evidence, **3 Decide** your own number. The coach checks your reading but never tells you the answer.")
        with st.expander("Step 0 · Big picture first (2 minutes)"):
            st.markdown("1. **Weather maps** (tab 2): where are the highs, lows and fronts? High to the north → poyraz; low to the north-west → lodos.\n"
                        "2. **Latest METARs:** wind, cloud, temperature–dew point gap, pressure rising or falling.\n"
                        "3. **Write your first idea** (section A).")
        A = state["assist"].setdefault(TARGET, {})
        if not model_names:
            st.caption("No model data yet. Press “Load latest data”.")
        for k in range(4):
            T = coach.COACH_TXT[k]
            a = A.setdefault(str(k), {})
            cur_f = S.get(f"f{k}_{TARGET}")
            with st.expander(f"{k + 1} · {T['name']} ({T['u']}) · {pretty_date(TARGET)}" + (f"  ✓ {cur_f:g} {T['u']}" if finite(cur_f) else ""), expanded=k == 0):
                st.caption(f"You forecast {T['what']}.")
                with st.popover("How is it measured and checked? · How do forecasters decide?"):
                    st.markdown(f"**Measurement.** {coach.MEASURE[k][0]}\n\n**How your forecast is checked.** {coach.MEASURE[k][1]}")
                    st.markdown("**How forecasters decide**\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(coach.DECIDE[k])))
                rows = [(n, coach_blocks(models, n, k, TARGET)) for n in model_names]
                rows = [(n, cb) for n, cb in rows if cb]
                if not rows:
                    st.caption("No model covers your day yet.")
                    continue
                best = sorted([n for n, _ in rows], key=lambda n: (live_bias(prev, obs, n, k, NOW) or {}).get("mae") or 99)[0]
                m = a.get("model") if a.get("model") in [n for n, _ in rows] else best
                m = st.selectbox("My model (◀ = smallest recent error)", [n for n, _ in rows], index=[n for n, _ in rows].index(m),
                                 key=f"cm{k}_{TARGET}", disabled=locked, format_func=lambda n: n + (" ◀" if n == best else ""))
                if m != a.get("model"):
                    a["model"], a["read"] = m, None
                st.markdown(f"**Step 1 · Read the model.** The table shows the **{T['blocks']}** for {pretty_date(TARGET)}. {T['read']}")
                trows = []
                for n, cb in rows:
                    bb = live_bias(prev, obs, n, k, NOW) if k != 2 else None
                    trows.append([n + (" ◀" if n == m else "")] + [fmt(v, dk(k)) if finite(v) else "—" for v in cb["blocks"]]
                                 + ([f"{sgn(bb['bias'])} ({bb['n']} d)" if bb and bb["n"] else "—"] if k != 2 else []))
                show_df(trows, ["Model"] + coach.BLOCK_COLS[k] + (["Recent bias (+ = too high)"] if k != 2 else []))
                mine = dict(rows)[m]
                r1c, r2c = st.columns([2, 1])
                rd = r1c.number_input(f"What I read for {m} ({T['u']})", value=a.get("read"), step=0.1, key=f"rd{k}_{TARGET}", disabled=locked)
                if r2c.button("Check my reading", key=f"cc{k}", disabled=locked):
                    a["read"] = rd
                    save()
                if finite(a.get("read")):
                    if abs(a["read"] - mine["day"]) <= coach.COACH_TOL[k]:
                        st.success(f"✓ Correct reading of {m}. Now check the evidence below.")
                    else:
                        st.warning(T["miss"])
                st.markdown("**Step 2 · Check the evidence**")
                for q, d, tip in coach.evidence(live, k, TARGET, m, persist_values(), NOW):
                    st.markdown(f"- **{q}**  \n{d}  \n→ _{tip}_")
                st.markdown("**Step 3 · Decide your own number.** Start from your reading, correct it for the bias, then change it only where the evidence gives a reason. " + coach.MAE_TIP)
                if finite(a.get("read")) and abs(a["read"] - mine["day"]) <= coach.COACH_TOL[k] and k != 2:
                    bb = live_bias(prev, obs, m, k, NOW)
                    if bb and bb["n"]:
                        corr = a["read"] - bb["bias"]
                        st.info(f"My notes so far: {m} reading **{fmt(a['read'], dk(k))}** · recent bias {sgn(bb['bias'])} → bias-corrected **{fmt(max(0, corr) if k == 3 else corr, dk(k))}** {T['u']}.")
                f1, f2 = st.columns([2, 1])
                cf = f1.number_input(f"My forecast ({T['u']})", value=None, step=0.1, key=f"cf{k}_{TARGET}", disabled=locked)
                why = st.text_input("My reason in one line (it goes into your reasoning)", a.get("why", ""), key=f"cy{k}_{TARGET}", disabled=locked,
                                    placeholder=["e.g. model 19.4 minus its +0.5 bias; sunny afternoon", "e.g. windy, cloudy night: little cooling",
                                                 "e.g. no model and no TAF shows rain", "e.g. poyraz continues; TAF 12 kt, model 13 kt"][k])
                if why != a.get("why", ""):
                    a["why"] = why
                    save()
                if f2.button("Put it in my forecast form", key=f"cw{k}", type="primary", disabled=locked):
                    if not finite(cf):
                        st.error("Type your own number first.")
                    elif k >= 2 and cf < 0:
                        st.error("Precipitation and wind cannot be negative.")
                    else:
                        S[f"f{k}_{TARGET}"] = float(cf)
                        st.toast(f"{T['name']}: {cf:g} {T['u']} written into your forecast form (section D).")
        if st.button("Write my four reasons into the reasoning box (section D)", disabled=locked):
            t = " ".join(f"{coach.COACH_TXT[k]['name']}: {A[str(k)]['why'].rstrip('. ')}." for k in range(4) if A.get(str(k), {}).get("why"))
            if t:
                S[f"reasoning_{TARGET}"] = t + " What could make my forecast wrong: "
            else:
                st.error("Write a one-line reason in each value first.")

    # D · my forecast
    with st.container(border=True):
        st.markdown("#### D · My forecast")
        if trec:
            for k in range(4):
                S.setdefault(f"f{k}_{TARGET}", float(trec["f"][k]) if finite(trec["f"][k]) else None)
            S.setdefault(f"reasoning_{TARGET}", trec.get("reasoning", ""))
        with st.form("forecast"):
            cols = st.columns(4)
            fvals = [cols[k].number_input(LABELS[k], value=None, step=1.0 if k == 3 else 0.1, key=f"f{k}_{TARGET}", disabled=locked,
                                          placeholder=["e.g. 18.5", "e.g. 12", "e.g. 0.0", "e.g. 14"][k]) for k in range(4)]
            a, b = st.columns(2)
            cop = ["", "Low", "Medium", "High"]
            dop = (["", "I trust one model more", "I choose a value between the models", "I expect something different from the models"]
                   if len(model_names) > 1 else ["", "I follow the model", "I correct the model a little", "I expect something clearly different"])
            conf = a.selectbox("Confidence", cop, index=cop.index(trec["confidence"]) if trec and trec.get("confidence") in cop else 0, disabled=locked)
            deci = b.selectbox("My decision", dop, index=dop.index(trec["decision"]) if trec and trec.get("decision") in dop else 0, disabled=locked)
            prior = [x for x in str((trec or {}).get("decisionReason", "")).replace("Physical reasons: ", "").split("; ") if x in coach.REASONS]
            reasons = st.multiselect("Why does my forecast differ from the model? (optional physical reasons)", coach.REASONS, default=prior, disabled=locked)
            reasoning = st.text_area("My reasoning · two short sentences: why is my forecast different from the models (or why do I agree)?",
                                     key=f"reasoning_{TARGET}", disabled=locked, placeholder="What evidence supports your numbers? What could make the forecast wrong?")
            changed = st.text_input("Optional · “I changed my first idea because …”", (trec or {}).get("changedBecause", ""), disabled=locked)
            receipt = st.text_input("Official course-system receipt (optional)", (trec or {}).get("officialReceipt", ""))
            sure = st.checkbox("I have checked my four numbers. Lock them when I press Submit (they cannot be changed afterwards).", disabled=locked)
            x1, x2, x3 = st.columns(3)
            do_check = x1.form_submit_button("Check my forecast", disabled=locked)
            do_draft = x2.form_submit_button("Save draft (can still change)", disabled=locked)
            do_submit = x3.form_submit_button("Submit forecast (final · locks the values)", type="primary", disabled=locked)

        def warnings_for(f):
            w = []
            for k in (0, 1):
                v = [models[m]["daily"].get(TARGET, [None] * 4)[k] for m in models]
                v = [x for x in v if finite(x)]
                if finite(f[k]) and v and (f[k] < min(v) - 2 or f[k] > max(v) + 2):
                    w.append(f"{LABELS[k]} is more than 2 °C outside the range of the models. That can be valid: explain why in your reasoning.")
            pv2 = [x for x in (models[m]["daily"].get(TARGET, [None] * 4)[2] for m in models) if finite(x)]
            if finite(f[2]) and f[2] >= 20 and (not pv2 or max(pv2) < 5):
                w.append(f"Precipitation {f[2]} mm is large while the models give little rain. Did you type a probability (%) instead of mm?")
            wv = [x for x in (models[m]["daily"].get(TARGET, [None] * 4)[3] for m in models) if finite(x)]
            if finite(f[3]) and wv and f[3] > max(wv) + 10:
                w.append(f"Wind {f[3]} kt is far above every model. Did you use a gust instead of the maximum sustained wind?")
            if reasoning.strip() and len([s for s in reasoning.replace("!", ".").replace("?", ".").split(". ") if s.strip()]) < 2:
                w.append("Your reasoning has one sentence. Two short sentences are expected.")
            if not deci:
                w.append("Choose your decision type.")
            if TARGET not in cal:
                w.append(f"{TARGET} is not in the scheduled calendar; it will not count for participation.")
            return w

        if do_check or do_draft or do_submit:
            f = [float(x) if x is not None else None for x in fvals]
            errs = selftest.validate_forecast(f, reasoning, conf)
            warns = warnings_for(f)
            if do_submit and not sure and not errs:
                errs.append("Tick “I have checked my four numbers” first, then press Submit.")
            for e in errs:
                st.error(e)
            for w in warns:
                st.warning(w)
            if do_check and not errs:
                st.success("Reality check passed. This button only checks; nothing is saved or submitted.")
            if (do_draft or do_submit) and not errs:
                cb = S.get("curB") or {"date": YEST, "v": [None] * 4, "src": ["Typed by student"] * 4, "captured": stamp(now_ms())}
                if not valid_vector(cb["v"]):
                    st.error("Persistence values are inconsistent (Tmin > Tmax or a negative value).")
                else:
                    n = now_ms()
                    clim_t = clim_for(state.get("clim"), TARGET)
                    rec = {"date": TARGET, "f": f, "o": (trec or {}).get("o", [None] * 4), "oSrc": (trec or {}).get("oSrc", [""] * 4),
                           "obsSaved": (trec or {}).get("obsSaved", ""), "b": cb["v"], "bSrc": " | ".join(cb["src"]),
                           "baselineDate": YEST, "baselineCaptured": cb["captured"], "c": clim_t["v"] if clim_t else None,
                           "cSrc": clim_t["src"] if clim_t else "", "saved": stamp(n), "submitted": "", "receipt": "",
                           "officialReceipt": receipt.strip(), "confidence": conf, "decision": deci,
                           "decisionReason": ("Physical reasons: " + "; ".join(reasons)) if reasons else "",
                           "reasoning": reasoning.strip(), "changedBecause": changed.strip(), "firstIdea": state["firstIdeas"].get(TARGET),
                           "models": {}, "ens": None, "source": "Student forecast; model values saved from live open sources at submission time."}
                    if do_submit:
                        rec["submitted"] = stamp(n)
                        rec["receipt"] = f"LOCAL-{TARGET}-{iso_local(n)[11:19].replace(':', '')}"
                        rec["models"] = {m: list(models[m]["daily"][TARGET]) for m in models if any(finite(x) for x in models[m]["daily"].get(TARGET, []))}
                        es = ens_day_stats(live["ens"], TARGET)
                        rec["ens"] = {"system": es["system"], "n": es["members"],
                                      "p10": [r1(s["p10"]) if finite(s["p10"]) else None for s in es["st"]],
                                      "p50": [r1(s["med"]) if finite(s["med"]) else None for s in es["st"]],
                                      "p90": [r1(s["p90"]) if finite(s["p90"]) else None for s in es["st"]],
                                      "wet": round(es["wet"]) if finite(es["wet"]) else None} if es else None
                    old = storage.find_rec(state, TARGET)
                    if old and old.get("submitted"):
                        st.error("This forecast is already submitted and locked. A second submission is refused.")
                    else:
                        if old:
                            state["records"].remove(old)
                        state["records"].append(rec)
                        state["records"].sort(key=lambda r: r["date"])
                        save()
                        if do_submit:
                            st.success(f"Forecast submitted: {rec_status(rec)} at {nice(rec['submitted'])}. Values are locked. "
                                       "Enter the same numbers in the official course system if there is one, and save a CSV backup (tab 5).")
                            st.balloons()
                            st.rerun()
                        else:
                            st.info("Draft saved. It has NOT been submitted. Press Submit before 21:00:00.")
        if locked:
            st.success(f"✓ Submitted {nice(trec['submitted'])} ({rec_status(trec)}). Your numbers are locked and saved in data/state.json. "
                       "Your teacher does not see this page: hand in the same numbers through the official submission, and keep your CSV backup.")
            if receipt.strip() != trec.get("officialReceipt", ""):
                trec["officialReceipt"] = receipt.strip()
                save()
            ms = [m for m in models if any(finite(x) for x in models[m]["daily"].get(TARGET, []))]
            if ms:
                st.markdown(f"**Model disagreement for {TARGET} (after your submission)**")
                TH = [[1, 3], [1, 3], [1, 5], [3, 8]]
                rows = []
                for k in range(4):
                    v = [models[m]["daily"][TARGET][k] for m in ms]
                    b = [x for x in v if finite(x)]
                    sp = max(b) - min(b) if len(b) > 1 else None
                    rows.append([LABELS[k]] + [fmt(x, dk(k)) for x in v] + [fmt(avg(b)), fmt(trec["f"][k], dk(k)), fmt(sp),
                                 "N/A" if sp is None else "Close" if sp <= TH[k][0] else "Large" if sp >= TH[k][1] else "Moderate"])
                show_df(rows, ["Variable"] + ms + ["Mean", "Mine", "Spread", "Disagreement"])
    with st.container(border=True):
        st.markdown("#### Saved records (submitted values are locked)")
        show_df([[r["date"], nice(r["submitted"]) if r.get("submitted") else "not submitted", rec_status(r)] + [fmtm(v, dk(k)) for k, v in enumerate(r["f"])]
                 + [r.get("confidence") or "—", r.get("baselineDate") or "missing"] for r in state["records"]],
                ["Valid date", "Submitted (Istanbul)", "Status"] + LABELS + ["Confidence", "Persistence date"], "No records yet.")


# ====================================================================== TAB 1 · CHECK YESTERDAY
with tab_err:
    st.subheader("What happened to my forecast?")
    st.error("EVERY DAY COMPARE YESTERDAY’S FORECAST WITH YESTERDAY’S OBSERVATIONS. Write what you learned before you make your next forecast.")
    recs = sorted(state["records"], key=lambda r: r["date"], reverse=True)
    if not recs:
        st.caption("No saved forecast yet. After your first forecast day has ended, it appears here.")
    else:
        done = [r["date"] for r in recs if NOW > day_end(r["date"])]
        vd = st.selectbox("Saved forecast (valid date)", [r["date"] for r in recs], index=[r["date"] for r in recs].index(done[0]) if done else 0,
                          format_func=lambda d: f"{d} · {rec_status(storage.find_rec(state, d))}")
        r = storage.find_rec(state, vd)
        ended = NOW > day_end(vd)
        pr_ended = NOW >= pr_window(vd)[1]
        if not ended:
            st.info(f"Not checkable yet. Check it from 00:00 {shift(vd, 1)} (temperature, wind) and after 09:00 {shift(vd, 1)} (precipitation).")
        else:
            okey = f"oSrc_{vd}"
            if okey not in S:
                S[okey] = list(r.get("oSrc") or [""] * 4)
            b1, b2 = st.columns(2)
            if b1.button("Fill Tmax, Tmin, wind from LTFM METAR", type="primary"):
                with st.spinner(f"Requesting LTFM METARs for {vd}…"):
                    d, stt = sources.fetch_obs_for(vd)
                if not d:
                    st.error("METAR archive not available: " + stt.get("error", "no reports") + ". You can type the instructor-approved values.")
                else:
                    for k, v in ((0, d["tmax"]), (1, d["tmin"]), (3, d["wind"])):
                        S[f"o{k}_{vd}"] = float(v) if finite(v) else None
                        S[okey][k] = f"LTFM METAR via IEM ({d['n']} reports)" if finite(v) else "missing"
                    if dry_day(d) and S.get(f"o2_{vd}") is None:
                        S[f"o2_{vd}"] = 0.0
                        S[okey][2] = f"No precipitation in any of {d['pn']} METARs (09:00→09:00) → 0.0 mm"
                    st.success(f"Filled from {d['n']} METAR reports. Precipitation day: " + ("not finished yet." if not d["prComplete"] else
                               f"precipitation in {d['prN']} reports: enter the MGM 24-hour total (06–06 UTC) from your instructor." if d["prN"]
                               else "no precipitation reported → 0.0 mm.") + " Then press “Save observations”.")
            if b2.button("Precipitation from model analysis (labelled, not scored)"):
                v, stt = sources.fetch_analysis_for(vd)
                if finite(v):
                    S[f"o2_{vd}"] = float(v)
                    S[okey][2] = "Model analysis (Open-Meteo) — not an observation"
                else:
                    st.error("Model analysis not available.")
            manual_o = st.toggle("METAR not available? Type Tmax, Tmin, wind by hand (marked in your report)", key="manualObs")
            cols = st.columns(4)
            ov = []
            for k in range(4):
                S.setdefault(f"o{k}_{vd}", float(r["o"][k]) if finite(r["o"][k]) else None)
                dis = (k != 2 and not manual_o) or (k == 2 and not pr_ended)
                ov.append(cols[k].number_input("Observed " + LABELS[k], value=None, step=0.1, key=f"o{k}_{vd}", disabled=dis))
                cols[k].caption(S[okey][k] or "source: —")
            if st.button("Save observations"):
                o = [float(x) if x is not None else None for x in ov]
                if not valid_vector(o):
                    st.error("Observations are inconsistent (Tmin > Tmax or a negative value).")
                else:
                    src = list(S[okey])
                    for k in range(4):
                        if finite(o[k]) and finite(r["o"][k]) and o[k] != r["o"][k] and not src[k]:
                            src[k] = "Typed by student"
                        if finite(o[k]) and not src[k]:
                            src[k] = "Typed by student (MGM 06–06 UTC amount from instructor)" if k == 2 else "Typed by student (manual entry)"
                    r["o"], r["oSrc"], r["obsSaved"] = o, [s if finite(o[k]) else "missing" for k, s in enumerate(src)], stamp(now_ms())
                    save()
                    st.success(f"Observations saved for {vd}. Blank values stay missing and are excluded from the scores.")
            st.caption("Tmax, Tmin and wind come from the LTFM METARs. Precipitation: type the MGM 24-hour total (06–06 UTC) your instructor gives; "
                       "0.0 is filled automatically when no METAR reported precipitation. A blank value stays missing, never 0.")
            rows = []
            for k in range(4):
                o, fv, b, c = r["o"][k], r["f"][k], (r.get("b") or [None] * 4)[k], (r.get("c") or [None] * 4)[k]
                e = fv - o if finite(o) and finite(fv) else None
                pe = b - o if finite(o) and finite(b) else None
                why = exclusion(r, k, NOW, cal)
                res = why or ("Tie" if abs(abs(e) - abs(pe)) < 1e-9 else "✔ Win: I beat persistence" if abs(e) < abs(pe) else "✘ Loss: persistence won")
                rows.append([LABELS[k], fmt(fv, dk(k)), (fmt(o, dk(k)) + f" ({(r.get('oSrc') or [''] * 4)[k] or 'source not recorded'})") if finite(o) else "missing",
                             fmtm(b), fmtm(c), sgn(e), fmt(abs(e) if finite(e) else None), sgn(pe), res])
            st.caption(f"LTFM · {vd} · submitted {nice(r.get('submitted'))} · persistence day {r.get('baselineDate')}")
            show_df(rows, ["Variable", "My forecast", "Observation (source)", "Persistence", "Climatology", "My error", "My abs. error", "Persistence error", "Result / exclusion"])
            ms = list((r.get("models") or {}).keys())
            if ms:
                st.markdown("**Each model’s error for this day (values saved at submission)**")
                show_df([[m] + [f"{sgn(r['models'][m][k] - r['o'][k])} (model {fmt(r['models'][m][k])})" if finite(r["o"][k]) and finite(r["models"][m][k]) else "N/A" for k in range(4)] for m in ms]
                        + [["Me"] + [sgn(r["f"][k] - r["o"][k]) if finite(r["o"][k]) and finite(r["f"][k]) else "N/A" for k in range(4)]],
                        ["Model"] + [l + " error" for l in LABELS])
            st.caption("Error = forecast − observation. Positive temperature error = too warm. Win / Tie / Loss compares your absolute error with the persistence absolute error.")

            st.markdown("#### My daily learning note (saved separately from the locked forecast)")
            n = state["notes"].get(vd, {})
            with st.form(f"note_{vd}"):
                big = st.text_area("My biggest error was…", n.get("biggest", ""))
                rea = st.text_area("A possible reason is…", n.get("reason", ""))
                cs = st.multiselect("Possible causes (not proven)", coach.CAUSES, default=[c for c in n.get("causes", []) if c in coach.CAUSES])
                chk = st.text_area("For my next forecast, I will check…", n.get("check", ""))
                if st.form_submit_button("Save learning note", type="primary"):
                    state["notes"][vd] = {"biggest": big.strip(), "reason": rea.strip(), "check": chk.strip(), "causes": cs, "savedAt": stamp(now_ms())}
                    save()
                    st.success(f"Learning note saved for {vd}.")
            with st.expander("Prepare an AI question about this error (copy it into your AI chat; write the learning note yourself)"):
                lines = "\n".join(f"- {LABELS[k]}: my forecast {fmt(r['f'][k])}, observation {fmtm(r['o'][k])} ({(r.get('oSrc') or [''] * 4)[k] or 'source not recorded'}), "
                                  f"persistence {fmtm((r.get('b') or [None] * 4)[k])}" + (", models " + ", ".join(f"{m} {fmt(v[k])}" for m, v in r["models"].items()) if r.get("models") else "")
                                  for k in range(4))
                st.code(f"Here are my saved forecast for Istanbul Airport (LTFM) for {vd}, the observation and the information available before submission.\n{lines}\n"
                        f"My reasoning before submission: {r.get('reasoning') or '—'}\n\nExplain the errors in simple English. Suggest possible causes, but separate "
                        "evidence from guesses. Ask me what I should check next time. Do not invent weather data or write my personal learning note for me.", language=None)


# ====================================================================== TAB 4 · PROGRESS
with tab_prog:
    p = participation(cal, state["records"], NOW)
    st.subheader("Am I taking part regularly?")
    st.error("AT LEAST 70% PARTICIPATION IS COMPULSORY. Participation and accuracy are shown separately.")
    (st.success if p["rate"] is not None and p["rate"] >= 70 else st.warning)(participation_text(p))
    a, b, c, d = st.columns(4)
    a.metric("Participation to date", fmt(p["rate"]) + "%" if p["rate"] is not None else "N/A", f"{p['done']} / {p['elapsed']} elapsed days", delta_color="off")
    b.metric("On-time complete days", p["valid"])
    c.metric("Minimum required", p["required"], f"70% of {p['total']}", delta_color="off")
    d.metric("Additional days needed", p["remaining"])
    cells = []
    for dd in cal:
        rr = storage.find_rec(state, dd)
        pend = deadline(dd) >= NOW
        ok = bool(rr and complete(rr) and ontime(rr) and parse_ts(rr["submitted"]) <= NOW)
        cls, sym = ("ok", "✓") if ok else ("pend", "○") if pend else ("no", "L" if rr and rr.get("submitted") and not ontime(rr) else "I" if rr and rr.get("submitted") else "—")
        cells.append(f"<span class='ltfm-day {cls}' title='{dd} · {rec_status(rr) if rr else 'missed / pending'}'><b>{dd[8:]}</b>{dd[5:7]} {sym}</span>")
    st.markdown("".join(cells), unsafe_allow_html=True)
    st.caption("✓ on time and complete · L late · I incomplete · — missed / not submitted · ○ deadline pending")

    st.subheader("Me vs. Persistence")
    allr = sorted(state["records"], key=lambda r: r["date"])
    dmin = _date.fromisoformat(cal[0]) if cal else _date.today()
    dmax = _date.fromisoformat(cal[-1]) if cal else _date.today()
    rng = st.date_input("Score and report period (never changes participation)", (dmin, dmax), min_value=dmin, max_value=dmax)
    lo, hi = (rng[0].isoformat(), rng[1].isoformat()) if isinstance(rng, (list, tuple)) and len(rng) == 2 else (cal[0] if cal else "", cal[-1] if cal else "")
    rows_sel = [r for r in allr if lo <= r["date"] <= hi]
    S.rows_sel = rows_sel
    sc = [scores(rows_sel, k, NOW, cal) for k in range(4)]
    if not any(s["n"] for s in sc):
        st.info("Your scores appear after your first checked day: the day after your first forecast is valid, once you have filled the observations in “Check yesterday”.")
    show_df([[LABELS[k], s["n"], s["excluded"], fmt(s["mae"], 2), sgn(s["bias"], 2), fmt(s["base"], 2), fmt(s["skill"]),
              f"{s['w']} / {s['t']} / {s['l']}", fmt(s["skillPoints"]), fmt(s["winPoints"]), fmt(s["overall"])] for k, s in enumerate(sc)],
            ["Variable", "Cases", "Excluded", "My MAE", "Bias", "Persistence MAE", "Skill %", "W / T / L", "Skill pts", "Win pts", "Overall"])
    st.caption("Skill (%) = 100 × (1 − my MAE ÷ persistence MAE). Positive = you beat persistence. Skill points = skill limited to 0–100; "
               "win points = 100 × (wins + 0.5 × ties) ÷ cases; overall = 0.5 × skill points + 0.5 × win points. Not a course grade.")
    kv = st.selectbox("Variable for the charts", range(4), format_func=lambda k: LABELS[k])
    ser = skill_series(rows_sel, kv, NOW, cal)
    if ser:
        xs = [s["date"] for s in ser]
        f1 = go.Figure([go.Scatter(x=xs, y=[s["cum"] for s in ser], mode="lines+markers", name="Cumulative skill %", line=dict(color="#006f68")),
                        go.Scatter(x=xs, y=[s["roll"] for s in ser], mode="lines", name="7-case moving skill %", line=dict(color="#be762f", dash="dash"))])
        f1.add_hline(y=0, line_color="#173a40")
        f1.update_layout(title="My skill score over time (line at 0 = equal to persistence)", height=300, margin=dict(l=40, r=10, t=40, b=30))
        st.plotly_chart(f1, key="skill")
        f2 = go.Figure([go.Scatter(x=xs, y=[s["abs"] for s in ser], mode="markers", name="Daily absolute error", marker=dict(color="#5b4fb3")),
                        go.Scatter(x=xs, y=[s["ma"] for s in ser], mode="lines", name="7-case moving average", line=dict(color="#a11c25"))])
        f2.update_layout(title="My daily absolute error", height=300, margin=dict(l=40, r=10, t=40, b=30), yaxis_title=UNITS[kv])
        st.plotly_chart(f2, key="err")
    with st.expander("Why were cases excluded?"):
        show_df([[r["date"]] + [exclusion(r, k, NOW, cal) or "Included" for k in range(4)] for r in rows_sel], ["Target"] + LABELS)
    st.markdown("**Compared with climatology** (persistence remains the official baseline)")
    show_df([[LABELS[k], c["n"], fmt(c["mae"], 2), fmt(c["cmae"], 2), fmt(c["skill"])] for k in range(4) for c in [clim_scores(rows_sel, k, NOW, cal)]],
            ["Variable", "Cases with climatology", "My MAE", "Climatology MAE", "Skill vs climatology %"])
    st.markdown("**Which model has been best for LTFM? (same eligible days)**")
    for k in range(4):
        msx = model_scores(rows_sel, k, NOW, cal)
        if msx:
            st.caption(LABELS[k])
            show_df([[i + 1, m["model"], m["n"], fmt(m["mae"], 2), sgn(m["bias"], 2), fmt(m["me"], 2)] for i, m in enumerate(msx)],
                    ["Rank", "Model", "Days", "Model MAE", "Model bias", "My MAE on the same days"])
    ended_dates = sorted(r["date"] for r in state["records"] if day_end(r["date"]) < NOW)[-7:]
    r7 = [r for r in state["records"] if r["date"] in ended_dates]
    st.markdown("**Last 7 days compared with the whole period**")
    show_df([[LABELS[k], a7["n"], fmt(a7["mae"], 2), fmt(a7["skill"]), sc[k]["n"], fmt(sc[k]["mae"], 2), fmt(sc[k]["skill"])]
             for k in range(4) for a7 in [scores(r7, k, NOW, cal)]],
            ["Variable", "Last 7: cases", "Last 7: MAE", "Last 7: skill %", "Whole: cases", "Whole: MAE", "Whole: skill %"])
    st.markdown("**Monthly progress**")
    months = sorted({d[:7] for d in cal if lo <= d <= hi})
    show_df([[mo, fmt(participation([d for d in cal if d.startswith(mo)], state["records"], NOW)["rate"]) + "%"] +
             [f"{fmt(s['mae'], 2)} / {fmt(s['skill'])} ({s['n']})" for k in range(4) for s in [scores([r for r in rows_sel if r['date'].startswith(mo)], k, NOW, cal)]]
             for mo in months], ["Month", "Participation"] + [l + " MAE / skill %" for l in LABELS])
    st.markdown("**Were my high-confidence forecasts more accurate?**")
    show_df([[cf, len(rr)] + [f"{fmt(s['mae'], 2)} ({s['n']})" if s["n"] else "N/A" for k in range(4) for s in [scores(rr, k, NOW, cal)]]
             for cf in ("Low", "Medium", "High") for rr in [[r for r in rows_sel if r.get("confidence") == cf]]],
            ["Confidence", "Forecasts"] + [l + " MAE" for l in LABELS])
    st.markdown("**My tendency**")
    for k in range(4):
        s = sc[k]
        if s["n"]:
            a7 = scores(r7, k, NOW, cal)
            st.markdown(f"- **{LABELS[k]}**: bias {sgn(s['bias'], 2)} {UNITS[k]} over {s['n']} cases → "
                        + ("no clear tendency" if abs(s["bias"]) < .3 else "you tend to forecast too HIGH" if s["bias"] > 0 else "you tend to forecast too LOW")
                        + (f"; last 7 days MAE {fmt(a7['mae'], 2)} vs whole period {fmt(s['mae'], 2)} " + ("(improving)" if finite(a7["mae"]) and a7["mae"] < s["mae"] else "(not improving yet)") if a7["n"] else ""))


# ====================================================================== TAB 5 · REPORT
with tab_rep:
    st.subheader("Save your work and build your report")
    st.markdown("Save a CSV backup after every entry. At the end of the semester open the report, print it as PDF and name it "
                "**MTO111E_StudentID_Forecast_Report.pdf**. Write the reflection yourself.")
    Sx = state["settings"]
    with st.form("settings"):
        refl1 = st.text_area("My reflection · What did I learn?", Sx.get("reflLearn", ""))
        refl2 = st.text_area("My reflection · What did I change?", Sx.get("reflChange", ""))
        refl3 = st.text_area("My reflection · Where should I improve?", Sx.get("reflImprove", ""))
        ailog = st.text_area("My AI-use log · tool, useful prompts, what I changed, a mistake I checked", Sx.get("aiLog", ""))
        if st.form_submit_button("Save details", type="primary"):
            Sx.update(reflLearn=refl1, reflChange=refl2, reflImprove=refl3, aiLog=ailog)
            save()
            st.success("Details saved.")
    a, b, c = st.columns(3)
    csv_text = "﻿" + storage.export_csv(state)
    if a.download_button("⬇ Download CSV backup", csv_text.encode("utf-8"), f"LTFM_live_backup_{TODAY}.csv", "text/csv"):
        state["lastBackup"] = TODAY
        save()
    if b.button("💾 Save CSV backup into the backups/ folder"):
        pth = storage.write_backup(state)
        state["lastBackup"] = TODAY
        save()
        st.success(f"Saved {pth.name} in the backups folder. Commit it to GitHub if you keep your log there.")
    rows_rep = S.get("rows_sel", sorted(state["records"], key=lambda r: r["date"]))
    c.download_button("🖨 Download printable report (HTML → Print → Save as PDF)", report.build_report(state, rows_rep).encode("utf-8"),
                      f"MTO111E_{(Sx.get('studentId') or 'StudentID')}_Forecast_Report.html", "text/html",
                      disabled=not (Sx.get("name") and Sx.get("studentId")),
                      help="Write your name and student number in the sidebar first.")
    st.caption("Your records are stored in data/state.json on this computer. The CSV is your backup, compatible with the HTML dashboard’s CSV files "
               "(UTF-8, commas, quotation marks around every cell).")
    up = st.file_uploader("Upload CSV to restore (adds new dates, skips existing dates, rebuilds all scores)", type=["csv"])
    if up is not None and st.button("Restore from this CSV"):
        try:
            new, added, skipped = storage.import_csv(up.getvalue().decode("utf-8-sig"), state)
            S.state = storage.sync_calendar(new)
            storage.save_state(S.state)
            st.success(f"CSV restored: {added} new dates added; {skipped} existing dates kept unchanged.")
            st.rerun()
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            st.error(str(e))
    with st.expander("For the instructor · technical self-test of the dashboard"):
        if st.button("Run checks now"):
            res = selftest.run_checks()
            st.write(f"**{sum(v for _, v in res)} / {len(res)} PASS**")
            show_df([[i + 1, n, "PASS" if v else "FAIL"] for i, (n, v) in enumerate(res)], ["#", "Check", "Result"])

st.caption("Data sources: Iowa Environmental Mesonet (LTFM METAR) · Open-Meteo forecast, previous-runs, ensemble and ERA5 APIs · MET Norway Locationforecast · "
           "NOAA Aviation Weather Center (TAF, METAR) · MGM · ECMWF open charts (CC BY 4.0) · Windy.com. Credit every source you use.")
