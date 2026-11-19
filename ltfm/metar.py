"""METAR and TAF decoding (plain English), as in the original dashboard."""
from __future__ import annotations

import re

WX = {'-': 'light', '+': 'heavy', 'VC': 'in the vicinity', 'RE': 'recent', 'MI': 'shallow', 'BC': 'patches',
      'PR': 'partial', 'DR': 'low drifting', 'BL': 'blowing', 'SH': 'shower(s)', 'TS': 'thunderstorm',
      'FZ': 'freezing', 'DZ': 'drizzle', 'RA': 'rain', 'SN': 'snow', 'SG': 'snow grains', 'IC': 'ice crystals',
      'PL': 'ice pellets', 'GR': 'hail', 'GS': 'small hail', 'UP': 'unknown precipitation', 'BR': 'mist',
      'FG': 'fog', 'FU': 'smoke', 'VA': 'volcanic ash', 'DU': 'dust', 'SA': 'sand', 'HZ': 'haze',
      'PO': 'dust whirls', 'SQ': 'squalls', 'FC': 'funnel cloud', 'SS': 'sandstorm', 'DS': 'duststorm'}
CLOUD = {'FEW': 'few (1–2 eighths)', 'SCT': 'scattered (3–4 eighths)', 'BKN': 'broken (5–7 eighths)',
         'OVC': 'overcast (8 eighths)'}
PRECIP_RE = re.compile(r"(^|\s)(\+|-|VC|RE)?(SH|TS|FZ)?(DZ|RA|SN|SG|PL|GR|GS|UP)+(\s|$)")
WXTOK = re.compile(r"^(-|\+|VC|RE)?(MI|BC|PR|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)+$")
C16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
LOCAL = {'NE': 'poyraz', 'NNE': 'poyraz', 'ENE': 'poyraz', 'SW': 'lodos', 'SSW': 'lodos', 'WSW': 'lodos',
         'NW': 'karayel', 'SE': 'keşişleme', 'N': 'yıldız', 'S': 'kıble'}


def compass(d) -> str:
    if d is None:
        return ''
    return C16[int((d / 22.5) + 0.5) % 16]


def local_wind(d) -> str:
    return LOCAL.get(compass(d), '') if d is not None else ''


def body(raw: str) -> str:
    """The observation part of a METAR, without remarks and trend groups."""
    s = str(raw or '')
    for cut in (' RMK', ' BECMG', ' TEMPO', ' NOSIG'):
        s = s.split(cut)[0]
    return s


def has_precip(raw: str) -> bool:
    return bool(raw) and bool(PRECIP_RE.search(body(raw)))


def _t(s):
    if not s:
        return None
    return -int(s[1:]) if s[0] == 'M' else int(s)


def decode_metar(raw: str) -> list[tuple[str, str]]:
    out = []
    toks = [t for t in str(raw or '').strip().upper().rstrip('=').split() if t]
    trend = False

    def dirtxt(d):
        n = int(d)
        sector = '(north-east · poyraz sector)' if 30 <= n <= 60 else '(south-west · lodos sector)' if 200 <= n <= 240 else ''
        return f"{n:03d}° {sector}".strip()

    for i, t in enumerate(toks):
        if trend:
            out.append((t, 'part of the trend forecast for the next 2 hours'))
            continue
        if t in ('METAR', 'SPECI'):
            out.append((t, 'routine observation report' if t == 'METAR' else 'special report (significant change)'))
        elif re.fullmatch(r'[A-Z]{4}', t) and i <= 1:
            out.append((t, 'Istanbul Airport' if t == 'LTFM' else 'station ICAO code'))
        elif (m := re.fullmatch(r'(\d{2})(\d{2})(\d{2})Z', t)):
            out.append((t, f"day {m[1]} of the month, {m[2]}:{m[3]} UTC = {(int(m[2]) + 3) % 24:02d}:{m[3]} Istanbul"))
        elif t in ('AUTO', 'COR'):
            out.append((t, 'fully automatic report' if t == 'AUTO' else 'corrected report'))
        elif (m := re.fullmatch(r'(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?(KT|MPS)', t)):
            u = ' kt' if m[5] == 'KT' else ' m/s'
            txt = 'wind from ' + ('variable direction' if m[1] == 'VRB' else dirtxt(m[1])) + f" at {int(m[2])}{u}"
            if m[1] != 'VRB' and m[5] == 'KT':
                txt += f" (≈ {round(int(m[2]) * 1.852)} km/h)"
            if m[4]:
                txt += f", gusts to {int(m[4])}{u} (a gust is not the sustained wind)"
            out.append((t, txt))
        elif (m := re.fullmatch(r'(\d{3})V(\d{3})', t)):
            out.append((t, f"wind direction varying between {m[1]}° and {m[2]}°"))
        elif t == 'CAVOK':
            out.append((t, 'ceiling and visibility OK: visibility ≥ 10 km, no cloud below 5,000 ft, no significant weather'))
        elif (m := re.fullmatch(r'(\d{4})(NDV)?', t)):
            out.append((t, 'visibility 10 km or more' if m[1] == '9999' else f"visibility {int(m[1])} m"))
        elif re.match(r'R\d{2}[LCR]?/', t):
            out.append((t, 'runway visual range (RVR) for a runway'))
        elif (m := re.fullmatch(r'(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?', t)):
            extra = (', cumulonimbus' if m[3] == 'CB' else ', towering cumulus') if m[3] else ''
            out.append((t, f"{CLOUD[m[1]]} at {int(m[2]) * 100:,} ft (≈ {round(int(m[2]) * 30.48)} m){extra}"))
        elif (m := re.fullmatch(r'VV(\d{3}|///)', t)):
            out.append((t, 'sky obscured, vertical visibility ' + ('unknown' if m[1] == '///' else f"{int(m[1]) * 100} ft")))
        elif t in ('NSC', 'NCD', 'SKC', 'CLR'):
            out.append((t, 'no significant cloud'))
        elif (m := re.fullmatch(r'(M?\d{2})/(M?\d{2})?', t)):
            T, D = _t(m[1]), _t(m[2])
            txt = f"temperature {T} °C / dew point " + ('missing' if D is None else f"{D} °C")
            if D is not None:
                txt += f" → gap {T - D} °C" + (' (air close to saturation: fog or low cloud possible)' if T - D <= 2 else '')
            out.append((t, txt))
        elif (m := re.fullmatch(r'Q(\d{4})', t)):
            out.append((t, f"pressure reduced to sea level (QNH) {int(m[1])} hPa (normal ≈ 1013 hPa)"))
        elif (m := re.fullmatch(r'A(\d{4})', t)):
            out.append((t, f"altimeter setting {int(m[1]) / 100:.2f} inHg"))
        elif t == 'NOSIG':
            out.append((t, 'no significant change expected in the next 2 hours'))
        elif t in ('BECMG', 'TEMPO'):
            out.append((t, 'becoming: trend forecast follows' if t == 'BECMG' else 'temporarily: trend forecast follows'))
            trend = True
        elif t == 'RMK':
            out.append((t, 'remarks follow (national use)'))
            trend = True
        elif t.startswith('WS'):
            out.append((t, 'wind shear reported'))
        elif (m := re.fullmatch(r'(-|\+|VC|RE)?(MI|BC|PR|DR|BL|SH|TS|FZ)?((?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)+)', t)):
            parts = [WX[x] for x in (m[1], m[2]) if x] + [WX[m[3][j:j + 2]] for j in range(0, len(m[3]), 2)]
            out.append((t, 'weather: ' + ' '.join(parts)))
        elif (m := re.fullmatch(r'(-|\+|VC|RE)?(SH|TS)', t)):
            out.append((t, 'weather: ' + ' '.join(WX[x] for x in (m[1], m[2]) if x)))
        else:
            out.append((t, 'not decoded here: look it up in your lecture notes'))
    return out


def parse_metar_fields(raw: str) -> dict:
    s = ' ' + body(raw) + ' '
    o = {'dir': None, 'spd': None, 'gust': None, 'T': None, 'Td': None, 'qnh': None, 'cloud': None, 'vis': None}
    if (m := re.search(r'\s(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\s', s)):
        o['dir'] = None if m[1] == 'VRB' else int(m[1])
        o['spd'] = int(m[2])
        o['gust'] = int(m[3]) if m[3] else None
    if (m := re.search(r'\s(M?\d{2})/(M?\d{2})\s', s)):
        o['T'], o['Td'] = _t(m[1]), _t(m[2])
    if (m := re.search(r'\sQ(\d{4})\s', s)):
        o['qnh'] = int(m[1])
    cov = {'FEW': 25, 'SCT': 50, 'BKN': 75, 'OVC': 100}
    c = None
    for x in re.findall(r'\s(FEW|SCT|BKN|OVC)\d{3}', s):
        c = max(c or 0, cov[x])
    if re.search(r'\s(CAVOK|NSC|NCD|SKC|CLR)\s', s):
        c = 0
    o['cloud'] = c
    if (m := re.search(r'\s(\d{4})\s', s)):
        o['vis'] = int(m[1])
    if ' CAVOK ' in s:
        o['vis'] = 9999
    o['precip'] = bool(PRECIP_RE.search(s))
    return o


def metar_groups(raw: str) -> dict:
    toks = body(raw).strip().split()
    cl = [t for t in toks if re.fullmatch(r'(FEW|SCT|BKN|OVC)\d{3}(CB|TCU)?|VV(\d{3}|///)|CAVOK|NSC|NCD|SKC|CLR', t)]
    wx = [t for i, t in enumerate(toks) if i > 1 and WXTOK.fullmatch(t)]
    return {'clouds': ' '.join(cl) or '—', 'wx': ' '.join(wx) or '—'}


def decode_taf(raw: str) -> list[tuple[str, str, str]]:
    """Split a TAF into groups: (heading, period in UTC and Istanbul time, plain-English meaning)."""
    toks = str(raw or '').strip().upper().split()
    segs = []
    cur = {'head': 'Main forecast', 'period': '', 'toks': []}

    def per(s):
        m = re.fullmatch(r'(\d{2})(\d{2})/(\d{2})(\d{2})', s)
        if not m:
            return s
        lt = lambda h: f"{(int(h) + 3) % 24:02d}"
        return f"day {m[1]} {m[2]}:00 UTC ({lt(m[2])}:00 Istanbul) to day {m[3]} {m[4]}:00 UTC ({lt(m[4])}:00 Istanbul)"

    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ('TAF', 'AMD', 'COR', 'LTFM') or re.fullmatch(r'\d{6}Z', t):
            i += 1
            continue
        if re.fullmatch(r'\d{4}/\d{4}', t) and not cur['period']:
            cur['period'] = per(t)
            i += 1
            continue
        if t in ('BECMG', 'TEMPO') or re.fullmatch(r'PROB\d{2}', t) or re.fullmatch(r'FM\d{6}', t):
            segs.append(cur)
            if t == 'BECMG':
                head = 'Becoming'
            elif t == 'TEMPO':
                head = 'Temporarily'
            elif t.startswith('PROB'):
                head = 'Probability ' + t[4:] + '%'
                if i + 1 < len(toks) and toks[i + 1] == 'TEMPO':
                    head += ' temporarily'
                    i += 1
            else:
                head = f"From day {t[2:4]} {t[4:6]}:{t[6:8]} UTC"
            cur = {'head': head, 'period': '', 'toks': []}
            i += 1
            continue
        cur['toks'].append(t)
        i += 1
    segs.append(cur)
    out = []
    for s in segs:
        d = [x[1].replace(' (north-east · poyraz sector)', ' (poyraz)').replace(' (south-west · lodos sector)', ' (lodos)')
             for x in decode_metar(' '.join(s['toks'])) if 'not decoded' not in x[1]]
        out.append((s['head'], s['period'], '; '.join(d) or '—'))
    return out


def metar_dew_gap(raw: str):
    """(temperature, dew point) from a METAR, or None."""
    m = re.search(r'\s(M?\d{2})/(M?\d{2})\s', ' ' + str(raw or '') + ' ')
    return (_t(m[1]), _t(m[2])) if m else None
