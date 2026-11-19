"""Scoring rules, METAR decoding and CSV round trip (same checks as 'Run checks now')."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ltfm.selftest import run_checks  # noqa: E402
from ltfm.sources import parse_iem, parse_om  # noqa: E402
from ltfm.metar import decode_taf  # noqa: E402

CHECKS = run_checks()


@pytest.mark.parametrize("name,ok", CHECKS, ids=[n for n, _ in CHECKS])
def test_rule(name, ok):
    assert ok, name


def test_parse_iem_and_om_precip_window():
    from tests.fakes import iem_text, om_json
    from ltfm.timeutil import parse_ts
    now = parse_ts("2026-11-19T19:30:00+03:00")
    reps = parse_iem(iem_text("2026-11-19", now))
    assert reps and reps[0]["ms"] < reps[-1]["ms"] and reps[-1]["metar"].startswith("LTFM")
    om = parse_om(om_json("2026-11-19"))
    # precipitation re-binned to 09→09 days, 24 hourly values each
    assert om["daily"]["2026-11-20"][2] is not None


def test_taf_decode():
    rows = decode_taf("TAF LTFM 191100Z 1912/2018 03015KT 9999 SCT030 BECMG 1920/1922 05010KT TEMPO 2009/2014 -SHRA BKN020")
    assert rows[0][0] == "Main forecast" and "poyraz" in rows[0][2]
    assert rows[1][0] == "Becoming" and rows[2][0] == "Temporarily" and "rain" in rows[2][2]
