"""Runs the whole app.py against a Streamlit stand-in and synthetic test answers (no internet, no Streamlit needed)."""
import importlib
import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _run(tmp_path, monkeypatch, now, clicks=(), state=None, keep=None):
    from tests import fakes
    monkeypatch.setenv("LTFM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LTFM_NOW", now)
    for m in [m for m in sys.modules if m.startswith("ltfm")]:
        del sys.modules[m]
    from ltfm import sources, storage
    from ltfm.timeutil import local_day, now_ms
    storage.DATA_DIR = tmp_path
    storage.STATE_FILE = tmp_path / "state.json"
    if state is not None:
        storage.save_state(state)
    monkeypatch.setattr(sources, "fetch_source", fakes.fake_fetch_factory(local_day(now_ms()), now_ms()))
    monkeypatch.setattr(sources, "ecmwf_chart", lambda p, v: (None, "https://charts.ecmwf.int/", "test"))
    fake = fakes.FakeSt(clicks)
    if keep:
        fake.session_state.update(keep)
    fakes.install_fakes(fake)
    try:
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
    except fakes._Rerun:
        pass
    return fake, storage


def test_app_renders_empty(tmp_path, monkeypatch):
    fake, _ = _run(tmp_path, monkeypatch, "2026-11-19T19:30:00+03:00")
    errors = [c for c in fake.calls if c[0] == "error"]
    assert ("title", ("Istanbul Airport (LTFM) forecast dashboard",)) in fake.calls
    assert not any("Could not save" in str(c) for c in errors)


def test_submit_flow_and_check(tmp_path, monkeypatch):
    # 1) draft → submit with all fields filled
    keep = {"f0_2026-11-20": 16.5, "f1_2026-11-20": 8.0, "f2_2026-11-20": 0.0, "f3_2026-11-20": 13.0,
            "reasoning_2026-11-20": "Models agree on a sunny day. Poyraz keeps it cool."}
    import ltfm.timeutil  # noqa
    fake, storage = _run(tmp_path, monkeypatch, "2026-11-19T20:30:00+03:00",
                         clicks={"Submit forecast (final · locks the values)",
                                 "I have checked my four numbers. Lock them when I press Submit (they cannot be changed afterwards)."},
                         keep=keep)
    # selectbox for confidence/decision default to "" → validation error expected
    assert any(c[0] == "error" and "confidence" in str(c[1]).lower() for c in fake.calls)
    st = json.loads((tmp_path / "state.json").read_text()) if (tmp_path / "state.json").exists() else {"records": []}
    assert not st["records"]

    # 2) with confidence chosen (stand-in returns session value for keyed widgets only; patch selectbox)
    from tests import fakes
    orig = fakes.FakeSt.selectbox
    def sb(self, label, options, index=0, key=None, **k):
        opts = list(options)
        if label == "Confidence":
            return "Medium"
        if label == "My decision":
            return opts[2]
        return orig(self, label, options, index, key, **k)
    monkeypatch.setattr(fakes.FakeSt, "selectbox", sb)
    fake, storage = _run(tmp_path, monkeypatch, "2026-11-19T20:30:00+03:00",
                         clicks={"Submit forecast (final · locks the values)",
                                 "I have checked my four numbers. Lock them when I press Submit (they cannot be changed afterwards)."},
                         keep=keep)
    st = json.loads((tmp_path / "state.json").read_text())
    r = st["records"][0]
    assert r["date"] == "2026-11-20" and r["submitted"].startswith("2026-11-19T20:30:00")
    assert r["f"] == [16.5, 8.0, 0.0, 13.0] and r["baselineDate"] == "2026-11-18"
    assert all(v is not None for v in (r["b"][0], r["b"][1], r["b"][3])) and r["models"]

    # 3) second submission is refused (form is locked)
    fake, storage = _run(tmp_path, monkeypatch, "2026-11-19T20:45:00+03:00",
                         clicks={"Submit forecast (final · locks the values)"}, keep=keep)
    st2 = json.loads((tmp_path / "state.json").read_text())
    assert st2["records"][0]["submitted"] == r["submitted"]

    # 4) two days later: fill observations from METAR and save, then scores exist
    fake, storage = _run(tmp_path, monkeypatch, "2026-11-21T19:00:00+03:00",
                         clicks={"Fill Tmax, Tmin, wind from LTFM METAR"})
    keep2 = {k: v for k, v in fake.session_state.items() if k.startswith(("o", "oSrc"))}
    assert keep2.get("o0_2026-11-20") is not None
    fake, storage = _run(tmp_path, monkeypatch, "2026-11-21T19:00:00+03:00", clicks={"Save observations"}, keep=keep2)
    st3 = json.loads((tmp_path / "state.json").read_text())
    rr = st3["records"][0]
    assert rr["o"][0] is not None and "METAR" in rr["oSrc"][0]
    from ltfm.scoring import scores
    from ltfm.timeutil import parse_ts
    sc = scores(st3["records"], 0, parse_ts("2026-11-21T19:00:00+03:00"), st3["calendar"])
    assert sc["n"] == 1


def test_with_real_backup(tmp_path, monkeypatch):
    bk = ROOT / "tests" / "data" / "sample_backup.csv"
    if not bk.exists():
        pytest.skip("no sample backup")
    for m in [m for m in sys.modules if m.startswith("ltfm")]:
        del sys.modules[m]
    from ltfm import storage
    s, added, _ = storage.import_csv(bk.read_text(encoding="utf-8-sig"), storage.sync_calendar(storage.blank_state()))
    assert added > 0
    fake, _ = _run(tmp_path, monkeypatch, "2026-11-19T19:30:00+03:00", state=s)
    assert ("title", ("Istanbul Airport (LTFM) forecast dashboard",)) in fake.calls
