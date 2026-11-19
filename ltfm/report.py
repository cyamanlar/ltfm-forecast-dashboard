"""Printable end-of-semester report (HTML → print to PDF as MTO111E_StudentID_Forecast_Report.pdf)."""
from __future__ import annotations

import html

from .config import LABELS, UNITS
from .scoring import clim_scores, exclusion, model_scores, participation, participation_text, rec_status, scores
from .timeutil import day_end, deadline, finite, nice, now_ms, parse_ts, stamp

esc = lambda x: html.escape(str(x if x is not None else ""))


def fmt(x, d=1):
    return f"{x:.{d}f}" if finite(x) else "N/A"


def sgn(x, d=1):
    return f"{x:+.{d}f}" if finite(x) else "N/A"


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows) or \
        f"<tr><td colspan='{len(headers)}'>No records in this period.</td></tr>"
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


CSS = """body{font:10.5pt/1.45 Georgia,serif;color:#1c2b36;max-width:960px;margin:20px auto;padding:0 14px}
h1{font-size:18pt;margin:0 0 6px}h2{font-size:14pt;margin:18px 0 6px;border-bottom:2px solid #1f5f8b}
table{border-collapse:collapse;width:100%;font-size:9pt;margin:6px 0}th,td{border:1px solid #c9d6de;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#dfeaf1;-webkit-print-color-adjust:exact;print-color-adjust:exact}.em{color:#861721;font-weight:700}
.pre{white-space:pre-line}@page{size:A4;margin:14mm}section{break-before:page}section:first-of-type{break-before:auto}
tr{break-inside:avoid}"""


def build_report(state, rows):
    now = now_ms()
    S = state["settings"]
    cal = state["calendar"]
    p = participation(cal, state["records"], now)
    elapsed = [d for d in cal if deadline(d) < now]
    sub = [r for r in state["records"] if r["date"] in elapsed and r.get("submitted") and parse_ts(r["submitted"]) <= now]
    late = len([r for r in sub if rec_status(r).startswith("LATE")])
    missed = len([d for d in elapsed if not any(r["date"] == d for r in sub)])
    period = f"{rows[0]['date'] if rows else '—'} to {rows[-1]['date'] if rows else '—'}"
    sc = [scores(rows, k, now, cal) for k in range(4)]
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>MTO111E_{esc(S.get('studentId') or 'StudentID')}_Forecast_Report</title><style>{CSS}</style></head><body>"]
    h.append(f"<section><h1>Forecast report · Istanbul Airport (LTFM)</h1><p><b>{esc(S.get('name') or 'Name not entered')}</b> · "
             f"student no. {esc(S.get('studentId') or '—')}<br>Course MTO111E · İTÜ İklim Bilimi ve Meteoroloji Mühendisliği Bölümü<br>"
             f"Reporting period {esc(period)} · target day 00:00–24:00 Istanbul (UTC+3), precipitation 09:00→09:00 · export {esc(nice(stamp(now)))}</p>"
             "<p class='em'>Self-check only. Official scores are calculated by the instructor from the official submissions and observations.</p>")
    h.append(f"<h2>1. Compulsory participation</h2><p class='em'>At least 70% of scheduled days is required. {esc(participation_text(p))}.</p>"
             + table(["On-time complete", "Scheduled / elapsed", "Required / additional", "Participation to date"],
                     [[p["valid"], f"{p['total']} / {p['elapsed']}", f"{p['required']} / {p['remaining']}", fmt(p["rate"]) + "%"]])
             + f"<p>Elapsed days submitted: {len(sub)}; late: {late}; missed: {missed}.</p>")
    h.append("<h2>2. Forecast performance for each variable</h2>" + table(
        ["Variable", "Eligible cases", "MAE", "Bias", "Persistence MAE", "Skill %", "W / T / L"],
        [[LABELS[k], s["n"], fmt(s["mae"], 2), sgn(s["bias"], 2), fmt(s["base"], 2), fmt(s["skill"]), f"{s['w']} / {s['t']} / {s['l']}"]
         for k, s in enumerate(sc)]) + "<p>Skill = 100 × (1 − MAE ÷ persistence MAE). Missing data are never zero.</p>")
    h.append("<h3>Compared with climatology and models</h3>" + table(
        ["Variable", "My MAE", "Climatology MAE", "Skill vs climatology %", "Best model (MAE, days)"],
        [[LABELS[k], fmt(c["mae"], 2), fmt(c["cmae"], 2), fmt(c["skill"]), (f"{m[0]['model']} ({fmt(m[0]['mae'], 2)}, {m[0]['n']})" if m else "N/A")]
         for k in range(4) for c, m in [(clim_scores(rows, k, now, cal), model_scores(rows, k, now, cal))]]) + "</section>")
    # learning examples
    bw = []
    for k in range(4):
        v = sorted(sc[k]["pairs"], key=lambda r: abs(r["f"][k] - r["o"][k]))
        f = lambda r: f"{r['date']} / {abs(r['f'][k] - r['o'][k]):.1f}" if r else "N/A"
        note = state["notes"].get(v[-1]["date"], {}) if v else {}
        bw.append([LABELS[k], f(v[0] if v else None), f(v[-1] if v else None), (note.get("biggest", "") + " " + note.get("reason", "")).strip() or "—"])
    h.append("<section><h2>3. Learning examples</h2>" + table(["Variable", "Best date / abs. error", "Largest error date / abs. error", "My note on the largest error"], bw))
    h.append("<h3>Confidence test</h3>" + table(["Confidence", "Forecasts"] + [l + " MAE" for l in LABELS],
             [[c, len(rr)] + [fmt(scores(rr, k, now, cal)["mae"], 2) for k in range(4)]
              for c in ("Low", "Medium", "High") for rr in [[r for r in rows if r.get("confidence") == c]]]) + "</section>")
    h.append(f"<section><h2>4. Technology use and reflection</h2><h3>AI-use log</h3><p class='pre'>{esc(S.get('aiLog') or 'Not entered')}</p>"
             f"<h3>What did I learn?</h3><p class='pre'>{esc(S.get('reflLearn') or 'Not entered')}</p>"
             f"<h3>What did I change?</h3><p class='pre'>{esc(S.get('reflChange') or 'Not entered')}</p>"
             f"<h3>Where should I improve?</h3><p class='pre'>{esc(S.get('reflImprove') or 'Not entered')}</p>"
             "<h3>Data sources and method</h3><p>Observations: LTFM METAR via Iowa Environmental Mesonet. Models: Open-Meteo (ECMWF IFS, GFS, ICON, "
             "Météo-France, UKMO), MET Norway; ensemble: Open-Meteo ensemble API; climatology: ERA5 1991–2020; TAF/METAR: NOAA AWC. "
             "Persistence = observed values of the last completed Istanbul day before submission, saved with date and capture time.</p></section>")
    app = []
    for r in rows:
        ended = now > day_end(r["date"])
        cells = []
        for k in range(4):
            o = r["o"][k] if ended and r.get("o") else None
            e = r["f"][k] - o if finite(o) and finite(r["f"][k]) else None
            cells.append(" / ".join(fmt(v) for v in (r["f"][k], o, (r.get("b") or [None] * 4)[k], e)))
        app.append([r["date"] + " / " + (nice(r.get("submitted")) if r.get("submitted") else "not submitted"), rec_status(r), *cells])
    h.append(f"<section><h2>5. Daily-record appendix</h2><p>{len(rows)} records. F / O / P / E = forecast / observation / persistence / signed error.</p>"
             + table(["Target / submitted", "Status"] + LABELS, app))
    notes = []
    for r in rows:
        n = state["notes"].get(r["date"], {})
        notes.append([r["date"], (r.get("reasoning") or "") + (f" · {r['decision']}" if r.get("decision") else ""),
                      " | ".join(x for x in (n.get("biggest"), n.get("reason"), n.get("check")) if x) or "—",
                      "; ".join(f"{LABELS[k].split(' (')[0]}: {exclusion(r, k, now, cal) or 'included'}" for k in range(4))])
    h.append("<h3>Reasoning, learning notes and exclusions</h3>" + table(["Target", "Reasoning", "Learning note", "Exclusions"], notes) + "</section>")
    h.append("<section><h2>6. Overall success scores</h2>" + table(
        ["Variable", "Eligible cases", "Raw skill %", "Skill points", "Win points", "Overall / 100"],
        [[LABELS[k], s["n"], fmt(s["skill"]), fmt(s["skillPoints"]), fmt(s["winPoints"]), fmt(s["overall"])] for k, s in enumerate(sc)])
        + "<p class='pre'>Skill points = skill limited to 0–100. Win points = 100 × (wins + 0.5 × ties) ÷ cases.\n"
          "Overall = 0.5 × skill points + 0.5 × win points. The four values are not a combined course grade.</p></section>")
    h.append("<script>window.onload=()=>setTimeout(()=>window.print(),400)</script></body></html>")
    return "".join(h)
