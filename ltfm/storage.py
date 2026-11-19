"""Saving the student's records (data/state.json) and the CSV backup.

The CSV format is exactly the one of the HTML dashboard (39 columns), so the
backups made with the HTML page can be restored here and the other way round.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
from pathlib import Path

from .config import COURSE
from .scoring import valid_vector
from .timeutil import local_day, now_ms, parse_ts, shift, valid_date

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LTFM_DATA_DIR", ROOT / "data"))
STATE_FILE = DATA_DIR / "state.json"
BACKUP_DIR = ROOT / "backups"

COLS = ['kind', 'mode', 'date', 'submitted', 'saved', 'receipt', 'officialReceipt', 'f0', 'f1', 'f2', 'f3',
        'o0', 'o1', 'o2', 'o3', 'oSrc', 'obsSaved', 'b0', 'b1', 'b2', 'b3', 'bSrc', 'baselineDate',
        'baselineCaptured', 'c0', 'c1', 'c2', 'c3', 'cSrc', 'confidence', 'decision', 'decisionReason',
        'reasoning', 'changedBecause', 'source', 'firstIdea_json', 'models_json', 'ens_json', 'note_json']


def blank_state() -> dict:
    return {"schema": 2, "mode": "live", "calendar": [], "records": [], "notes": {}, "firstIdeas": {},
            "assist": {}, "clim": None, "climMeta": None, "lastBackup": None, "formDraft": None,
            "settings": {"name": "", "studentId": "", "reflLearn": "", "reflChange": "", "reflImprove": "",
                         "aiLog": "", "from": "", "to": ""}}


def normalize(s: dict) -> dict:
    b = blank_state()
    for k, v in b.items():
        if k not in s or (isinstance(v, dict) and not isinstance(s.get(k), dict)):
            s[k] = copy.deepcopy(v)
    s["settings"] = {**b["settings"], **(s.get("settings") or {})}
    recs = []
    for r in s.get("records") or []:
        if not isinstance(r, dict) or not valid_date(r.get("date")) or not isinstance(r.get("f"), list) or len(r["f"]) != 4:
            continue
        for k in ("o", "b"):
            if not isinstance(r.get(k), list) or len(r[k]) != 4:
                r[k] = [None] * 4
        if not isinstance(r.get("oSrc"), list):
            r["oSrc"] = ["", "", "", ""]
        recs.append(r)
    s["records"] = recs
    s["calendar"] = [d for d in s.get("calendar") or [] if valid_date(d)]
    return s


def load_state() -> dict:
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return sync_calendar(blank_state())
    try:
        s = json.loads(raw)
        if s.get("schema") == 2 and isinstance(s.get("records"), list):
            return sync_calendar(normalize(s))
    except ValueError:
        pass
    # keep a copy of an unreadable file, never delete student data
    try:
        STATE_FILE.rename(STATE_FILE.with_suffix(f".unreadable-{now_ms()}.json"))
    except OSError:
        pass
    return sync_calendar(blank_state())


def save_state(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def sync_calendar(s: dict) -> dict:
    if COURSE.get("start") and valid_date(COURSE["start"]):
        end = COURSE["end"] if valid_date(COURSE.get("end", "")) else shift(local_day(now_ms()), 1)
        out, d = [], COURSE["start"]
        while d <= end and len(out) < 400:
            if d not in COURSE.get("breaks", []):
                out.append(d)
            d = shift(d, 1)
        s["calendar"] = out
        s["calendarFixed"] = True
        return s
    ds = sorted(r["date"] for r in s["records"])
    out = []
    if ds:
        d, t = ds[0], shift(local_day(now_ms()), 1)
        while d <= t and len(out) < 400:
            out.append(d)
            d = shift(d, 1)
    s["calendar"] = out
    return s


def find_rec(s: dict, d: str):
    return next((r for r in s["records"] if r["date"] == d), None)


# ---------------------------------------------------------------- CSV
def csv_safe(v) -> str:
    s = "" if v is None else str(v)
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        return s
    return "'" + s if re.match(r"^'*[=+\-@\t\r]", s) else s


def _num(x):
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def csv_encode(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for r in rows:
        w.writerow([csv_safe(v) for v in r])
    return buf.getvalue().rstrip("\r\n")


def csv_decode(text: str):
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return [[c[1:] if re.match(r"^'+[=+\-@\t\r]", c) else c for c in r] for r in rows]


def export_csv(s: dict) -> str:
    meta = {"schema": 2, "mode": s["mode"], "calendar": s["calendar"], "settings": s["settings"],
            "checklist": s.get("checklist", {}), "firstIdeas": s["firstIdeas"], "climMeta": s.get("climMeta")}
    rows = [COLS, ["settings", s["mode"], "", "", "", "", "", json.dumps(meta, ensure_ascii=False)]]
    for r in s["records"]:
        vec = lambda key: [_num((r.get(key) or [None] * 4)[k]) for k in range(4)]
        rows.append(["record", s["mode"], r["date"], r.get("submitted", ""), r.get("saved", ""), r.get("receipt", ""),
                     r.get("officialReceipt", ""), *vec("f"), *vec("o"), " | ".join(r.get("oSrc") or []),
                     r.get("obsSaved", ""), *vec("b"), r.get("bSrc", ""), r.get("baselineDate", ""),
                     r.get("baselineCaptured", ""), *vec("c"), r.get("cSrc", ""), r.get("confidence", ""),
                     r.get("decision", ""), r.get("decisionReason", ""), r.get("reasoning", ""),
                     r.get("changedBecause", ""), r.get("source", ""),
                     json.dumps(r.get("firstIdea"), ensure_ascii=False), json.dumps(r.get("models") or {}, ensure_ascii=False),
                     json.dumps(r.get("ens"), ensure_ascii=False), json.dumps(s["notes"].get(r["date"]), ensure_ascii=False)])
    return csv_encode(rows)


def import_csv(text: str, target: dict):
    """Adds new dates, skips existing dates. Returns (new_state, added, skipped)."""
    data = csv_decode(text)
    if not data or data[0] != COLS or len(data) < 2 or data[1][0] != "settings":
        raise ValueError("This is not an LTFM Forecast Lab Pro CSV.")
    if data[1][1] != target["mode"]:
        raise ValueError(f"This backup belongs to {data[1][1]} mode.")
    meta = json.loads(data[1][7])
    if not isinstance(meta.get("calendar"), list) or not all(valid_date(d) for d in meta["calendar"]):
        raise ValueError("Invalid calendar in the backup.")
    temp = copy.deepcopy(target)
    added = skipped = 0
    n = lambda x: None if x == "" else float(x) if not re.fullmatch(r"-?\d+", x) else int(x)
    for row in data[2:]:
        if not row or row == [""]:
            continue
        if len(row) != len(COLS) or row[0] != "record" or not valid_date(row[2]):
            raise ValueError("Invalid CSV record line.")
        g = lambda c: row[COLS.index(c)]
        r = {"date": g("date"), "submitted": g("submitted"), "saved": g("saved"), "receipt": g("receipt"),
             "officialReceipt": g("officialReceipt"), "f": [n(g(f"f{k}")) for k in range(4)],
             "o": [n(g(f"o{k}")) for k in range(4)],
             "oSrc": g("oSrc").split(" | ") if g("oSrc") else ["", "", "", ""], "obsSaved": g("obsSaved"),
             "b": [n(g(f"b{k}")) for k in range(4)], "bSrc": g("bSrc"), "baselineDate": g("baselineDate"),
             "baselineCaptured": g("baselineCaptured"), "c": [n(g(f"c{k}")) for k in range(4)], "cSrc": g("cSrc"),
             "confidence": g("confidence"), "decision": g("decision"), "decisionReason": g("decisionReason"),
             "reasoning": g("reasoning"), "changedBecause": g("changedBecause"), "source": g("source"),
             "firstIdea": json.loads(g("firstIdea_json") or "null"), "models": json.loads(g("models_json") or "{}"),
             "ens": json.loads(g("ens_json") or "null")}
        if all(x is None for x in r["c"]):
            r["c"] = None
        while len(r["oSrc"]) < 4:
            r["oSrc"].append("")
        if (not all(valid_vector(r[k]) for k in ("f", "o", "b")) or not valid_date(r["baselineDate"])
                or parse_ts(r["baselineCaptured"]) is None or parse_ts(r["saved"]) is None
                or (r["submitted"] and parse_ts(r["submitted"]) is None)):
            raise ValueError(f"Invalid weather values or time stamps for {r['date']}.")
        if any(x["date"] == r["date"] for x in temp["records"]):
            skipped += 1
            continue
        r["imported"] = True
        temp["records"].append(r)
        note = json.loads(g("note_json") or "null")
        if note:
            temp["notes"][r["date"]] = note
        added += 1
    temp["calendar"] = sorted(set(temp["calendar"]) | set(meta["calendar"]))
    if not target["records"]:
        temp["settings"] = {**temp["settings"], **(meta.get("settings") or {})}
        temp["firstIdeas"] = {**(meta.get("firstIdeas") or {}), **temp["firstIdeas"]}
    temp["records"].sort(key=lambda r: r["date"])
    return temp, added, skipped


def write_backup(s: dict) -> Path:
    """Writes backups/LTFM_live_backup_YYYY-MM-DD.csv (UTF-8 with BOM, opens in Excel)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    p = BACKUP_DIR / f"LTFM_live_backup_{local_day(now_ms())}.csv"
    p.write_text("﻿" + export_csv(s), encoding="utf-8", newline="")
    return p
