"""Self-checks of the scoring rules (the same checks as 'Run checks now' in the HTML dashboard)."""
from __future__ import annotations

import copy

from . import storage
from .metar import decode_metar, parse_metar_fields
from .observations import quant
from .scoring import ontime, participation, scores, valid_vector
from .timeutil import local_day, parse_ts, shift, stamp
from .config import KN


def _mk(d):
    return {"date": d, "f": [18, 10, 0, 4], "o": [16, 10, 0, 4], "b": [20, 10, 0, 4], "baselineDate": shift(d, -2),
            "baselineCaptured": shift(d, -1) + "T19:00:00+03:00", "submitted": shift(d, -1) + "T21:00:00+03:00"}


def validate_forecast(f, reasoning, confidence):
    from .config import LABELS
    errors = []
    if any(v is None for v in f):
        errors.append("Fill all four forecast values. Missing values are never replaced with zero.")
    if any(isinstance(v, float) and v != v for v in f):
        errors.append("Use numbers only (for example 21.5).")
    if f[0] is not None and f[1] is not None and f[1] > f[0]:
        errors.append("Tmin cannot be greater than Tmax.")
    for k, lo, hi, u in [(0, -30, 50, '°C'), (1, -30, 50, '°C'), (2, 0, 300, 'mm'), (3, 0, 150, 'kt')]:
        if f[k] is not None and not (lo <= f[k] <= hi):
            errors.append(f"{LABELS[k]} must be between {lo} and {hi} {u}.")
    if not str(reasoning or "").strip():
        errors.append("Write your reasoning (at least one sentence; two are recommended).")
    if confidence not in ("Low", "Medium", "High"):
        errors.append("Choose your confidence (Low, Medium or High).")
    return errors


def run_checks():
    tests = []
    t = lambda n, v: tests.append((n, v))
    cal = [shift("2026-01-01", i) for i in range(23)]
    time = parse_ts("2026-02-01T12:00:00Z")
    rows = [_mk(d) for d in cal]
    t("14/20 meets participation", participation(cal[:20], rows[:14], time)["met"])
    t("13/20 does not meet participation", not participation(cal[:20], rows[:13], time)["met"])
    p = participation(cal, rows[:16], time)
    t("16/23 does not meet participation (17 required)", p["required"] == 17 and not p["met"])
    t("Duplicate dates count once", participation(cal, rows + rows, time)["valid"] == 23)
    t("No elapsed deadline gives N/A", participation(cal, rows, parse_ts("2025-01-01"))["rate"] is None)
    at = lambda hms: {**rows[0], "submitted": shift(rows[0]["date"], -1) + "T" + hms + "+03:00"}
    t("20:59:59 is on time", ontime(at("20:59:59")))
    t("21:00:00 is on time", ontime(at("21:00:00")))
    t("21:00:01 is late", not ontime(at("21:00:01")))
    t("Stamp truncates to seconds (21:00:00.900 stays on time)",
      ontime({**rows[0], "submitted": stamp(parse_ts(shift(rows[0]["date"], -1) + "T21:00:00.900+03:00"))}))
    miss = {**rows[0], "o": [None, 10, 0, 4]}
    t("Missing observation still counts for participation", participation(cal, [miss], time)["valid"] == 1)
    t("Missing observation is excluded, not zero", scores([miss], 0, time, cal)["n"] == 0)
    t("Incomplete forecast does not count", participation(cal, [{**rows[0], "f": [18, 10, None, 4]}], time)["valid"] == 0)
    a = {**rows[0], "f": [17.5, 10, 0, 4], "b": [18, 10, 0, 4]}
    t("MAE 1.5 vs 2.0 gives +25% skill", scores([a], 0, time, cal)["skill"] == 25)
    t("Forecast 18, observation 16 gives error +2", scores([rows[0]], 0, time, cal)["bias"] == 2)
    t("Zero persistence MAE gives N/A", scores([{**rows[0], "b": [16, 10, 0, 4]}], 0, time, cal)["skill"] is None)
    t("No pairs gives N/A", scores([], 0, time, cal)["overall"] is None)
    t("Baseline captured after submission is excluded",
      scores([{**rows[0], "baselineCaptured": "2026-02-01T00:00:00Z"}], 0, time, cal)["n"] == 0)
    t("Baseline from an unfinished day is excluded",
      scores([{**rows[0], "baselineDate": shift(rows[0]["date"], -1)}], 0, time, cal)["n"] == 0)
    t("Skill below −100 is kept visible", scores([{**a, "f": [21, 10, 0, 4]}], 0, time, cal)["skill"] == -150)
    E = [[1, 2]] * 6 + [[3, 3]] * 2 + [[1.5, 1]] * 2
    R = [{**rows[i], "f": [20 + e, 10, 0, 4], "o": [20, 10, 0, 4], "b": [20 + pp, 10, 0, 4]} for i, (e, pp) in enumerate(E)]
    sc = scores(R, 0, time, cal)
    t("Overall example gives 47.5", sc["n"] == 10 and sc["skill"] == 25 and (sc["w"], sc["t"], sc["l"]) == (6, 2, 2)
      and abs(sc["overall"] - 47.5) < 1e-9)
    t("Tmin > Tmax is refused", "Tmin cannot be greater than Tmax." in validate_forecast([15, 20, 0, 4], "x. y.", "Low")
      and not valid_vector([15, 20, 0, 4]))
    t("Negative wind is refused", bool(validate_forecast([20, 15, 0, -1], "x. y.", "Low")) and not valid_vector([20, 15, 0, -1]))
    st = storage.blank_state()
    st["calendar"] = cal
    st["records"] = [{**_mk("2026-01-05"), "saved": "2026-01-04T18:00:00+03:00",
                      "reasoning": 'Poyraz; "soğuk" hava, şüpheli ölçüm; İĞÜÇŞÖ çğıöşü', "oSrc": ['a', 'b', 'c', 'd'],
                      "bSrc": "x", "c": None, "cSrc": "", "confidence": "High", "models": {"M": [1, 2, 3, 4]},
                      "ens": None, "firstIdea": None,
                      "changedBecause": '=SUM(A1); "\'=x" -3 °C, ç ğ ı İ ö ş ü\nnew line'}]
    back = storage.csv_decode(storage.export_csv(st))
    i, j = storage.COLS.index("reasoning"), storage.COLS.index("changedBecause")
    t("CSV round trip keeps semicolons, quotes, new lines and Turkish letters",
      back[2][i] == st["records"][0]["reasoning"] and back[2][j] == st["records"][0]["changedBecause"])
    empty = copy.deepcopy(st)
    empty["records"] = []
    res, added, _ = storage.import_csv(storage.export_csv(st), empty)
    t("CSV export → clear → restore gives identical scores",
      added == 1 and [scores(res["records"], k, time, cal)["mae"] for k in range(4)] == [scores(st["records"], k, time, cal)["mae"] for k in range(4)])
    t("Restore skips existing dates", storage.import_csv(storage.export_csv(st), st)[2] == 1)
    raw = "METAR LTFM 191720Z 03017G28KT 9999 SCT032 BKN080 12/07 Q1021 NOSIG"
    f, d = parse_metar_fields(raw), dict(decode_metar(raw))
    t("METAR decoder: wind, gust, temperature, dew point, QNH",
      f["dir"] == 30 and f["spd"] == 17 and f["gust"] == 28 and f["T"] == 12 and f["Td"] == 7 and f["qnh"] == 1021
      and "poyraz" in d["03017G28KT"] and "1021 hPa" in d["Q1021"] and f["cloud"] == 75)
    t("10 m/s = 19.43844 kt", abs(10 * KN - 19.43844) < 1e-9)
    t("Percentile P50 of 1..5 is 3 and P10 is 1.4", quant([1, 2, 3, 4, 5], .5) == 3 and abs(quant([1, 2, 3, 4, 5], .1) - 1.4) < 1e-9)
    t("Istanbul local day starts at 21:00 UTC", local_day(parse_ts("2026-10-03T21:00:00Z")) == "2026-10-04"
      and local_day(parse_ts("2026-10-03T20:59:59Z")) == "2026-10-03")
    return tests
