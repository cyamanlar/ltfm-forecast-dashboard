"""TEST-ONLY synthetic API answers and a minimal Streamlit stand-in.

These numbers are invented for testing the code paths. They are never shown to a
student: the dashboard itself only uses live data or saved real copies.
"""
from __future__ import annotations

import json
import math
import sys
import types
from unittest.mock import MagicMock

from ltfm.timeutil import HOUR, iso_local, iso_utc, local_day, local_ms, shift


def _hours(d0, d1):
    t, out = local_ms(d0), []
    while t < local_ms(d1):
        out.append(t)
        t += HOUR
    return out


def om_json(today, offset=0.0):
    hrs = _hours(shift(today, -1), shift(today, 3))
    T = [12 + offset + 5 * math.sin((int(iso_local(t)[11:13]) - 9) / 24 * 2 * math.pi) for t in hrs]
    days = sorted({local_day(t) for t in hrs})
    return {"hourly": {"time": [iso_local(t)[:16] for t in hrs], "temperature_2m": T, "dew_point_2m": [x - 4 for x in T],
                       "wind_speed_10m": [8 + 4 * math.sin(i / 7) for i in range(len(hrs))],
                       "wind_direction_10m": [40] * len(hrs), "wind_gusts_10m": [18] * len(hrs),
                       "cloud_cover": [40] * len(hrs), "precipitation": [0.2 if i % 17 == 0 else 0 for i in range(len(hrs))],
                       "pressure_msl": [1018 - i * .05 for i in range(len(hrs))]},
            "daily": {"time": days, "temperature_2m_max": [17 + offset] * len(days), "temperature_2m_min": [7 + offset] * len(days),
                      "precipitation_sum": [0.6] * len(days), "wind_speed_10m_max": [12] * len(days)}}


def prev_json(today):
    hrs = _hours(shift(today, -7), shift(today, 1))
    return {"hourly": {"time": [iso_local(t)[:16] for t in hrs],
                       "temperature_2m_previous_day1": [12 + 5 * math.sin((int(iso_local(t)[11:13]) - 9) / 24 * 2 * math.pi) for t in hrs],
                       "wind_speed_10m_previous_day1": [20] * len(hrs), "precipitation_previous_day1": [0] * len(hrs)}}


def metno_json(today):
    hrs = _hours(today, shift(today, 3))
    return {"properties": {"meta": {"updated_at": iso_utc(hrs[0]) + "Z"}, "timeseries": [
        {"time": iso_utc(t) + "Z", "data": {"instant": {"details": {"air_temperature": 11, "wind_speed": 5, "relative_humidity": 70,
                                                                    "wind_from_direction": 30, "cloud_area_fraction": 50,
                                                                    "air_pressure_at_sea_level": 1017}},
                                             "next_1_hours": {"details": {"precipitation_amount": 0}}}} for t in hrs]}}


def ens_json(today):
    hrs = _hours(today, shift(today, 3))
    h = {"time": [iso_local(t)[:16] for t in hrs]}
    for m in range(5):
        s = "" if m == 0 else f"_member{m:02d}"
        h["temperature_2m" + s] = [11 + m * .3] * len(hrs)
        h["precipitation" + s] = [0.1 * m] * len(hrs)
        h["wind_speed_10m" + s] = [10] * len(hrs)
    return {"hourly": h}


def iem_text(today, now):
    t = local_ms(shift(today, -9))
    lines = ["station,valid,tmpc,sknt,gust,metar"]
    while t <= now:
        u = iso_utc(t)
        T = round(11 + 5 * math.sin((int(iso_local(t)[11:13]) - 9) / 24 * 2 * math.pi))
        wx = " -RA" if int(u[11:13]) == 14 else ""
        tt = f"M{abs(T - 4):02d}" if T - 4 < 0 else f"{T - 4:02d}"
        metar = f"LTFM {u[8:10]}{u[11:13]}{u[14:16]}Z 03012KT 9999{wx} SCT030 {T:02d}/{tt} Q1018 NOSIG"
        lines.append(f"LTFM,{u[:16].replace('T', ' ')},{T},12,,{metar}")
        t += 30 * 60000
    return "\n".join(lines)


def fake_fetch_factory(today, now):
    def fake_fetch(key, label, url, kind="json", cache_key=None):
        st = {"key": key, "label": label, "url": url, "http": "200", "error": "", "time": "test", "ok": True}
        if key.startswith("iem"):
            return iem_text(today, now), st
        if key.startswith("m_"):
            return om_json(today, offset=len(key) % 3), st
        if key.startswith("p_"):
            return prev_json(today), st
        if key.startswith("analysis"):
            return om_json(today), st
        if key == "metno":
            return metno_json(today), st
        if key == "ens":
            return ens_json(today), st
        if key == "taf":
            u = iso_utc(now)
            d = int(u[8:10])
            return [{"rawTAF": f"TAF LTFM {d:02d}1100Z {d:02d}12/{d + 1:02d}18 03015KT 9999 SCT030 BECMG {d:02d}20/{d:02d}22 05010KT TEMPO {d + 1:02d}09/{d + 1:02d}14 -SHRA BKN020"}], st
        if key == "awcmetar":
            return [{"rawOb": "LTFM 191650Z 03012KT 9999 SCT030 12/08 Q1018 NOSIG"}], st
        st.update(ok=False, http="no response", error="test: not provided")
        return None, st
    return fake_fetch


# ---------------------------------------------------------------- Streamlit stand-in
class _SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


class FakeSt:
    def __init__(self, clicks=()):
        self.session_state = _SS()
        self.clicks = set(clicks)
        self.calls = []
        self.components = types.SimpleNamespace(v1=types.SimpleNamespace(iframe=lambda *a, **k: None))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _ctx(self, *a, **k):
        return self

    container = expander = form = popover = spinner = _ctx

    @property
    def sidebar(self):
        return self

    def __getattr__(self, name):  # anything else: record and return None
        def f(*a, **k):
            self.calls.append((name, a[:1]))
            return None
        return f

    def columns(self, spec, **k):
        return [self] * (spec if isinstance(spec, int) else len(spec))

    def tabs(self, names):
        return [self] * len(names)

    def cache_data(self, *a, **k):
        return lambda fn: fn

    def fragment(self, *a, **k):
        return lambda fn: fn

    def button(self, label, *a, **k):
        return label in self.clicks

    form_submit_button = download_button = button

    def toggle(self, label, *a, **k):
        return label in self.clicks

    checkbox = toggle

    def _keyed(self, key, default):
        if key is not None:
            if key not in self.session_state:
                self.session_state[key] = default
            return self.session_state[key]
        return default

    def text_input(self, label, value="", key=None, **k):
        return self._keyed(key, value)

    text_area = text_input

    def number_input(self, label, min_value=None, max_value=None, value="min", step=None, key=None, **k):
        if value == "min":
            value = min_value
        return self._keyed(key, value)

    def selectbox(self, label, options, index=0, key=None, **k):
        options = list(options)
        return self._keyed(key, options[index] if options else None)

    def radio(self, label, options, index=0, **k):
        return list(options)[index]

    def multiselect(self, label, options, default=None, key=None, **k):
        return self._keyed(key, list(default or []))

    def date_input(self, label, value=None, **k):
        return value

    def file_uploader(self, *a, **k):
        return None

    def rerun(self):
        raise _Rerun()


class _Rerun(Exception):
    pass


def install_fakes(fake):
    sys.modules["streamlit"] = fake
    sys.modules["streamlit.components"] = fake.components
    sys.modules["streamlit.components.v1"] = fake.components.v1
    go = MagicMock()
    plotly = types.ModuleType("plotly")
    plotly.graph_objects = go
    sys.modules["plotly"] = plotly
    sys.modules["plotly.graph_objects"] = go
