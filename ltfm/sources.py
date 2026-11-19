"""Live open data sources, with a saved copy of the last successful answer.

Real data only: either a live answer, or the last real answer saved on disk
(always labelled with its date and time). Nothing is invented.
The requests run in Python on the student's computer, so there are no browser
CORS problems (the TAF and AWC METAR load here, unlike in a local HTML file).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from . import config as C
from .metar import has_precip
from .timeutil import (DAY, HOUR, finite, local_day, nice, now_ms, parse_ts, pr_day_of, r1, shift, stamp)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
TIMEOUT = 20
HEADERS = {"User-Agent": "LTFM-Student-Dashboard/1.0 (ITU MTO111E course; python-requests)"}


# ---------------------------------------------------------------- fetching
def _cache_file(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:60]
    return CACHE_DIR / (safe + "_" + hashlib.md5(key.encode()).hexdigest()[:8] + ".json")


def cache_put(key: str, url: str, text: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(key).write_text(json.dumps({"t": stamp(now_ms()), "url": url, "body": text}), encoding="utf-8")
    except OSError:
        pass


def cache_get(key: str):
    try:
        c = json.loads(_cache_file(key).read_text(encoding="utf-8"))
        return c if isinstance(c.get("body"), str) and isinstance(c.get("t"), str) else None
    except (OSError, ValueError):
        return None


def fetch_source(key: str, label: str, url: str, kind: str = "json", cache_key: str | None = None):
    """Returns (data or None, status dict). Falls back to the saved real copy if the live request fails."""
    ck = key if cache_key is None else cache_key
    st = {"key": key, "label": label, "url": url, "http": "…", "error": "", "time": "", "ok": False, "snap": False}
    try:
        res = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        st["http"] = str(res.status_code)
        text = res.text
        if not res.ok:
            reason = ""
            try:
                reason = json.loads(text).get("reason", "")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {res.status_code}" + (f" · {reason}" if reason else ""))
        data = json.loads(text) if kind == "json" else text
        st["ok"] = True
        st["time"] = nice(stamp(now_ms()))
        cache_put(ck, url, text)
        return data, st
    except Exception as e:  # noqa: BLE001 — every failure is reported, never hidden
        if st["http"] == "…":
            st["http"] = "no response"
            st["error"] = f"{type(e).__name__}: {e} (no internet connection, or the source is not reachable)"
        else:
            st["error"] = str(e)
        c = cache_get(ck)
        if c:
            try:
                data = json.loads(c["body"]) if kind == "json" else c["body"]
                st["snap"] = True
                st["cachedAt"] = c["t"]
                st["reason"] = st["error"]
                st["error"] = f"Live request failed → showing saved real data from {nice(c['t'])[:16]} (reason: {st['reason']})"
                return data, st
            except ValueError:
                pass
        return None, st


# ---------------------------------------------------------------- parsers
def om_hourly_ms(t: str) -> int:
    return parse_ts(t + ":00+03:00")


def _f(v):
    return v if finite(v) else None


def pr_daily(times, pr, precedes):
    by = {}
    for i, t in enumerate(times):
        v = pr[i] if pr and i < len(pr) else None
        if not finite(v):
            continue
        by.setdefault(pr_day_of(t, precedes), []).append(v)
    return {d: (r1(sum(v)) if len(v) >= 20 else None) for d, v in by.items()}


def apply_pr_window(daily, times, pr, precedes):
    pd = pr_daily(times, pr, precedes)
    for d in daily:
        daily[d][2] = pd.get(d)
    for d, v in pd.items():
        if d not in daily:
            daily[d] = [None, None, v, None]
    return daily


def parse_om(j: dict) -> dict:
    daily = {}
    d = j.get("daily") or {}
    for i, t in enumerate(d.get("time") or []):
        def g(a, i=i):
            arr = d.get(a) or []
            return _f(arr[i]) if i < len(arr) else None
        daily[t] = [g("temperature_2m_max"), g("temperature_2m_min"), g("precipitation_sum"), g("wind_speed_10m_max")]
    h = j.get("hourly") or {}
    pick = lambda a: [_f(v) for v in (h.get(a) or [])]
    hourly = {"time": [om_hourly_ms(t) for t in (h.get("time") or [])], "temp": pick("temperature_2m"),
              "td": pick("dew_point_2m"), "ws": pick("wind_speed_10m"), "wd": pick("wind_direction_10m"),
              "gust": pick("wind_gusts_10m"), "cloud": pick("cloud_cover"), "pr": pick("precipitation"),
              "p": pick("pressure_msl"), "prPrecedes": True}
    apply_pr_window(daily, hourly["time"], hourly["pr"], True)
    return {"daily": daily, "hourly": hourly}


def parse_prev(j: dict) -> dict:
    h = j.get("hourly") or {}
    times = h.get("time") or []
    T = h.get("temperature_2m_previous_day1") or []
    W = h.get("wind_speed_10m_previous_day1") or []
    P = h.get("precipitation_previous_day1") or []
    by = {}
    for i, t in enumerate(times):
        b = by.setdefault(t[:10], {"t": [], "w": []})
        if i < len(T) and finite(T[i]):
            b["t"].append(T[i])
        if i < len(W) and finite(W[i]):
            b["w"].append(W[i] / 1.852)  # km/h -> kt
    pd = pr_daily([om_hourly_ms(t) for t in times], P, True)
    daily = {}
    for d, b in by.items():
        ok = len(b["t"]) >= 20
        daily[d] = [max(b["t"]) if ok else None, min(b["t"]) if ok else None, pd.get(d),
                    r1(max(b["w"])) if len(b["w"]) >= 20 else None]
    if not any(finite(x) for v in daily.values() for x in v):
        raise ValueError("no day-1 values in the response")
    return {"daily": daily}


def dew_from_rh(T, RH):
    if not finite(T) or not finite(RH) or RH <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(RH / 100) + a * T / (b + T)
    return r1(b * g / (a - g))


def parse_metno(j: dict) -> dict:
    by_day, hourly = {}, {k: [] for k in ("time", "temp", "ws", "td", "wd", "cloud", "pr", "p")}
    for s in ((j.get("properties") or {}).get("timeseries") or []):
        ms = parse_ts(s.get("time"))
        det = ((s.get("data") or {}).get("instant") or {}).get("details") or {}
        p1 = ((s.get("data") or {}).get("next_1_hours") or {}).get("details")
        T = _f(det.get("air_temperature"))
        W = det.get("wind_speed") * C.KN if finite(det.get("wind_speed")) else None
        hourly["time"].append(ms)
        hourly["temp"].append(T)
        hourly["ws"].append(W)
        hourly["td"].append(dew_from_rh(T, det.get("relative_humidity")))
        hourly["wd"].append(_f(det.get("wind_from_direction")))
        hourly["cloud"].append(_f(det.get("cloud_area_fraction")))
        hourly["p"].append(_f(det.get("air_pressure_at_sea_level")))
        pv = p1.get("precipitation_amount") if p1 else None
        hourly["pr"].append(_f(pv))
        b = by_day.setdefault(local_day(ms), {"t": [], "w": []})
        if T is not None:
            b["t"].append(T)
        if W is not None:
            b["w"].append(W)
    daily = {d: [max(b["t"]) if len(b["t"]) >= 20 else None, min(b["t"]) if len(b["t"]) >= 20 else None, None,
                 r1(max(b["w"])) if len(b["w"]) >= 20 else None] for d, b in by_day.items()}
    apply_pr_window(daily, hourly["time"], hourly["pr"], False)
    hourly["gust"] = []
    hourly["prPrecedes"] = False
    issued = ((j.get("properties") or {}).get("meta") or {}).get("updated_at")
    return {"daily": daily, "hourly": hourly, "issued": issued}


def parse_iem(text: str) -> list[dict]:
    lines = text.strip().splitlines()
    if not lines:
        return []
    head = lines[0].split(",")
    ix = lambda n: head.index(n) if n in head else -1
    iV, iT, iS, iG, iM = ix("valid"), ix("tmpc"), ix("sknt"), ix("gust"), ix("metar")
    if iV < 0 or iT < 0:
        raise ValueError("unexpected CSV header: " + lines[0][:80])

    def nv(s):
        try:
            v = float(s.strip())
            return v if math.isfinite(v) else None
        except (ValueError, AttributeError):
            return None

    out = []
    for line in lines[1:]:
        c = line.split(",")
        if len(c) <= iV:
            continue
        ms = parse_ts(c[iV].strip().replace(" ", "T") + ":00Z")
        if ms is None:
            continue
        out.append({"ms": ms, "tmpc": nv(c[iT]) if iT < len(c) else None,
                    "sknt": nv(c[iS]) if 0 <= iS < len(c) else None,
                    "gust": nv(c[iG]) if 0 <= iG < len(c) else None,
                    "metar": ",".join(c[iM:]).strip() if 0 <= iM < len(c) else ""})
    return sorted(out, key=lambda r: r["ms"])


def parse_ens(j: dict, system: str) -> dict:
    h = j.get("hourly") or {}
    import re
    keys = [k for k in h if re.fullmatch(r"temperature_2m(_member\d+)?", k)]
    times = [om_hourly_ms(t) for t in (h.get("time") or [])]
    members = []
    for k in keys:
        s = k[len("temperature_2m"):]
        f = lambda a: [_f(v) for v in (h.get(a + s) or [])]
        members.append({"temp": f("temperature_2m"), "pr": f("precipitation"), "ws": f("wind_speed_10m")})
    if not members:
        raise ValueError("no temperature_2m members in the response")
    return {"system": system, "time": times, "members": members}


def parse_clim(j: dict) -> dict:
    d = j["daily"]
    buckets = {}
    for i, t in enumerate(d["time"]):
        buckets.setdefault(t[5:], []).append([d["temperature_2m_max"][i], d["temperature_2m_min"][i],
                                              d["precipitation_sum"][i], d["wind_speed_10m_max"][i]])
    from datetime import date, timedelta
    ref = [(date(2024, 1, 1) + timedelta(days=i)).isoformat()[5:] for i in range(366)]
    clim = {}
    for i, md in enumerate(ref):
        pool = []
        for k in range(-7, 8):
            pool += buckets.get(ref[(i + k) % 366], [])
        vals = []
        for k in range(4):
            v = [x[k] for x in pool if finite(x[k])]
            vals.append(r1(sum(v) / len(v)) if v else None)
        clim[md] = vals
    return clim


# ---------------------------------------------------------------- loading everything
def load_all(ens_id: str = "icon_seamless_eps", need_clim: bool = False) -> dict:
    """Load every source in parallel. One failing source never stops the others."""
    from .observations import build_obs  # local import: avoids a cycle

    now = now_ms()
    today = local_day(now)
    jobs = {
        "iem": ("A · LTFM METAR archive (Iowa Environmental Mesonet)", C.url_iem(shift(today, -9), shift(today, 1)), "text"),
        "analysis": ("D · Open-Meteo model analysis (last 8 days)", C.url_analysis(shift(today, -8), today), "json"),
        "metno": ("E · MET Norway Locationforecast", C.URL_METNO, "json"),
        "ens": (f"C · Open-Meteo ensemble ({ens_id})", C.url_ens(ens_id), "json"),
        "taf": ("F · NOAA AWC TAF (JSON)", C.URL_TAF, "json"),
        "awcmetar": ("F · NOAA AWC METAR (JSON)", C.URL_METAR, "json"),
    }
    for m in C.MODELS:
        jobs["m_" + m["id"]] = (f"B · Open-Meteo {m['label']} ({m['id']})", C.url_model(m["id"]), "json")
        jobs["p_" + m["id"]] = (f"B′ · Open-Meteo previous runs, day-1 forecasts ({m['id']})", C.url_prev(m["id"]), "json")
    if need_clim:
        jobs["clim"] = ("Climatology · Open-Meteo historical (ERA5, 1991–2020)", C.URL_CLIM, "json")

    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {k: ex.submit(fetch_source, k, lab, url, kind, "ens:" + ens_id if k == "ens" else None)
                for k, (lab, url, kind) in jobs.items()}
        for k, f in futs.items():
            results[k] = f.result()

    live = {"status": {}, "models": {}, "prev": {}, "obs": None, "analysis": None, "ens": None,
            "taf": None, "awcmetar": None, "clim": None, "refreshedAt": now}

    def parse(key, fn):
        data, st = results[key]
        live["status"][key] = st
        if data is None:
            return None
        try:
            return fn(data)
        except Exception as e:  # noqa: BLE001
            st["ok"] = False
            st["error"] = f"Data could not be read: {e}"
            return None

    live["obs"] = parse("iem", lambda t: build_obs(parse_iem(t), now))
    for m in C.MODELS:
        pm = parse("m_" + m["id"], parse_om)
        if pm and any(finite(x) for v in pm["daily"].values() for x in v):
            live["models"][m["label"]] = pm
        pp = parse("p_" + m["id"], parse_prev)
        if pp:
            live["prev"][m["label"]] = pp
    mn = parse("metno", parse_metno)
    if mn:
        live["models"]["MET Norway"] = mn
    an = parse("analysis", parse_om)
    live["analysis"] = an["daily"] if an else None
    live["ens"] = parse("ens", lambda j: parse_ens(j, ens_id))
    live["taf"] = parse("taf", lambda j: [x for x in j if isinstance(x, dict) and isinstance(x.get("rawTAF"), str)])
    live["awcmetar"] = parse("awcmetar", lambda j: [x for x in j if isinstance(x, dict) and isinstance(x.get("rawOb"), str)])
    if need_clim:
        live["clim"] = parse("clim", parse_clim)
    return live


def fetch_obs_for(date_str: str):
    """METARs around one valid date (used by 'Check yesterday')."""
    from .observations import add_pr_window, daily_from_reports
    data, st = fetch_source("iem_v", f"A · LTFM METAR archive for checking ({date_str})",
                            C.url_iem(shift(date_str, -1), shift(date_str, 2)), "text", "iem_v:" + date_str)
    if data is None:
        return None, st
    reports = parse_iem(data)
    reps = [r for r in reports if local_day(r["ms"]) == date_str]
    if not reps:
        return None, st
    now = now_ms()
    return add_pr_window(daily_from_reports(reps, date_str, now), reports, now), st


def fetch_analysis_for(date_str: str):
    data, st = fetch_source("analysis_v", f"D · model analysis for checking ({date_str})",
                            C.url_analysis(date_str, shift(date_str, 1)), "json", "analysis_v:" + date_str)
    if data is None:
        return None, st
    return (parse_om(data)["daily"].get(date_str) or [None] * 4)[2], st


def ecmwf_chart(prod: str, valid: str | None):
    """Image link of an ECMWF open chart (CC BY 4.0). Returns (image_url or None, page_url, error)."""
    q = "?projection=opencharts_south_east_europe" + (f"&valid_time={valid}" if valid else "")
    api = f"https://charts.ecmwf.int/opencharts-api/v1/products/{prod}/{q}"
    page = f"https://charts.ecmwf.int/products/{prod}{q}"
    try:
        r = requests.get(api, timeout=15, headers=HEADERS)
        r.raise_for_status()
        href = (((r.json() or {}).get("data") or {}).get("link") or {}).get("href")
        if not href or not href.startswith("https://charts.ecmwf.int/"):
            raise ValueError("no valid image link")
        return href, page, ""
    except Exception as e:  # noqa: BLE001
        return None, page, str(e)
