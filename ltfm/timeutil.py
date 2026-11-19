"""Istanbul time helpers. Istanbul is UTC+3 all year (no daylight saving since 2016).

All instants are integers in milliseconds since 1970-01-01 UTC (as in the original
JavaScript dashboard), so the logic can be compared line by line.
"""
from __future__ import annotations

import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

H3 = 10_800_000
HOUR = 3_600_000
DAY = 86_400_000
PR_SHIFT = 9 * HOUR  # precipitation day starts 09:00 Istanbul = 06 UTC
WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_NOW_OVERRIDE: int | None = None  # used by the self-tests only


def set_now(ms: int | None) -> None:
    global _NOW_OVERRIDE
    _NOW_OVERRIDE = ms


def now_ms() -> int:
    if _NOW_OVERRIDE is not None:
        return _NOW_OVERRIDE
    env = os.environ.get("LTFM_NOW")  # e.g. 2026-11-19T20:30:00+03:00 for a dry run
    if env:
        return parse_ts(env)
    return int(time.time() * 1000)


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def r1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10  # JS Math.round(x*10)/10


def js_round(x: float) -> int:
    return math.floor(x + 0.5)


def parse_ts(s: str) -> int | None:
    """Parse an ISO time ('2026-10-05T20:06:59+03:00', '...Z', '2026-10-05') to ms."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        t += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1000))


def iso_local(ms: int) -> str:
    return datetime.fromtimestamp((ms + H3) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def local_day(ms: int) -> str:
    return iso_local(ms)[:10]


def stamp(ms: int) -> str:
    return iso_local((ms // 1000) * 1000) + "+03:00"


def nice(s: str | None) -> str:
    return str(s).replace("T", " ").replace("+03:00", " Istanbul") if s else "—"


def valid_date(s) -> bool:
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def shift(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def local_ms(d: str, hms: str = "00:00:00") -> int:
    return parse_ts(f"{d}T{hms}+03:00")


def deadline(d: str) -> int:
    """Deadline for valid date d: 21:00:00 Istanbul on the day before (21:00:00 is on time)."""
    return local_ms(shift(d, -1), "21:00:00")


def day_end(d: str) -> int:
    return local_ms(d, "23:59:59")


def day_start(d: str) -> int:
    return local_ms(d, "00:00:00")


def pr_window(d: str) -> tuple[int, int]:
    a = local_ms(d, "09:00:00")
    return a, a + DAY


def pr_day_of(ms: int, precedes: bool) -> str:
    """Precipitation day of an hourly value. precedes=True: the value is the total of the hour before ms."""
    return local_day((ms - HOUR if precedes else ms) - PR_SHIFT)


def ymd(d: str):
    x = date.fromisoformat(d)
    return x, WD[(x.weekday() + 1) % 7]


def pretty_date(d: str) -> str:
    x, wd = ymd(d)
    return f"{wd} {x.day} {MON[x.month - 1]} {x.year}"


def day_label(d: str) -> str:
    x, wd = ymd(d)
    return f"{wd} {x.day} {MON[x.month - 1]}"


def countdown_text(ms_left: int) -> str:
    if ms_left < 0:
        return "deadline passed"
    h = ms_left // HOUR
    m = (ms_left % HOUR) // 60000
    s = (ms_left % 60000) // 1000
    return f"{h} h {m:02d} min {s:02d} s"


def to_dt_local(ms: int) -> datetime:
    """Naive datetime in Istanbul wall-clock time (for chart axes)."""
    return datetime.fromtimestamp((ms + H3) / 1000, tz=timezone.utc).replace(tzinfo=None)
