"""Course settings, station and data-source addresses.

Only the instructor changes COURSE. Everything else is the same as in the
original HTML dashboard (LTFM Forecast Lab Pro specification).
"""
from urllib.parse import quote

STATION = "LTFM"
STATION_NAME = "Istanbul Airport"
LAT, LON = 41.2753, 28.7519
ELEVATION_M = 99
KN = 1.943844  # m/s -> kt

# Competition calendar (instructor setting). Leave start empty ('') for an automatic calendar.
COURSE = {"start": "2026-10-05", "end": "2026-12-31", "breaks": []}

VARS = [
    {"label": "Tmax", "unit": "°C"},
    {"label": "Tmin", "unit": "°C"},
    {"label": "Precipitation 09→09", "unit": "mm"},
    {"label": "Max sustained wind", "unit": "kt"},
]
LABELS = [v["label"] + " (" + v["unit"] + ")" for v in VARS]
UNITS = [v["unit"] for v in VARS]

MODELS = [
    {"id": "ecmwf_ifs025", "label": "ECMWF IFS"},
    {"id": "gfs_seamless", "label": "GFS"},
    {"id": "icon_seamless", "label": "ICON"},
    {"id": "meteofrance_seamless", "label": "Météo-France"},
    {"id": "ukmo_seamless", "label": "UKMO"},
]
MODEL_COLORS = {
    "ECMWF IFS": "#d1495b", "GFS": "#2d7dd2", "ICON": "#00916e",
    "Météo-France": "#8f5bd6", "UKMO": "#e0892b", "MET Norway": "#1d2b2e",
}
ENSEMBLES = ["icon_seamless_eps", "ecmwf_ifs025", "gfs_ensemble_025", "gem_global"]

OM_BASE = (
    "https://api.open-meteo.com/v1/forecast?latitude=41.2753&longitude=28.7519"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
    "&wind_speed_unit=kn&timezone=Europe%2FIstanbul"
)


def url_iem(d1: str, d2: str) -> str:
    a, b = d1.split("-"), d2.split("-")
    return (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=LTFM"
        "&data=tmpc,sknt,gust,metar"
        f"&year1={a[0]}&month1={int(a[1])}&day1={int(a[2])}"
        f"&year2={b[0]}&month2={int(b[1])}&day2={int(b[2])}"
        "&tz=UTC&format=onlycomma&latlon=no&missing=empty&trace=empty"
    )


def url_model(model_id: str) -> str:
    return (
        OM_BASE + "&forecast_days=3&past_days=1&hourly=temperature_2m,dew_point_2m,wind_speed_10m,"
        "wind_direction_10m,wind_gusts_10m,cloud_cover,precipitation,pressure_msl&models=" + model_id
    )


def url_analysis(d1: str, d2: str) -> str:
    return OM_BASE + f"&hourly=precipitation&start_date={d1}&end_date={d2}"


URL_METNO = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=41.2753&lon=28.7519"
URL_TAF = "https://aviationweather.gov/api/data/taf?ids=LTFM&format=json"
URL_METAR = "https://aviationweather.gov/api/data/metar?ids=LTFM&format=json&hours=24"
URL_CLIM = (
    "https://archive-api.open-meteo.com/v1/archive?latitude=41.2753&longitude=28.7519"
    "&start_date=1991-01-01&end_date=2020-12-31"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
    "&wind_speed_unit=kn&timezone=Europe%2FIstanbul&models=era5"
)


def url_prev(model_id: str) -> str:
    return (
        "https://previous-runs-api.open-meteo.com/v1/forecast?latitude=41.2753&longitude=28.7519"
        "&hourly=temperature_2m_previous_day1,wind_speed_10m_previous_day1,precipitation_previous_day1"
        "&timezone=Europe%2FIstanbul&past_days=7&forecast_days=1&models=" + model_id
    )


def url_ens(ens_id: str) -> str:
    return (
        "https://ensemble-api.open-meteo.com/v1/ensemble?latitude=41.2753&longitude=28.7519"
        "&hourly=temperature_2m,precipitation,wind_speed_10m&wind_speed_unit=kn"
        "&timezone=Europe%2FIstanbul&forecast_days=3&models=" + quote(ens_id)
    )


LINKS = [
    ("Latest METARs, last 24 hours (AWC)", "https://aviationweather.gov/api/data/metar?ids=LTFM&hours=24"),
    ("Current TAF for LTFM (AWC)", "https://aviationweather.gov/api/data/taf?ids=LTFM"),
    ("METAR/TAF viewer, decoded", "https://aviationweather.gov/data/metar/?id=LTFM&hours=24"),
    ("MGM surface and upper-air charts", "https://www.mgm.gov.tr/sondurum/guncel-haritalar.aspx"),
    ("MGM satellite images", "https://www.mgm.gov.tr/sondurum/uydu.aspx"),
    ("MGM radar images", "https://www.mgm.gov.tr/sondurum/radar.aspx"),
    ("ECMWF chart: sea-level pressure and rain", "https://charts.ecmwf.int/products/medium-mslp-rain"),
    ("ECMWF chart: 500 hPa height and 850 hPa temperature", "https://charts.ecmwf.int/products/medium-z500-t850"),
    ("MGM WRF model maps", "https://www.mgm.gov.tr/tahmin/wrf-model-harita.aspx?t=rainG"),
    ("Open-Meteo: compare models", "https://open-meteo.com/en/docs"),
    ("Open-Meteo: ensemble forecasts", "https://open-meteo.com/en/docs/ensemble-api"),
    ("Open-Meteo: past model runs", "https://open-meteo.com/en/docs/previous-runs-api"),
    ("MGM Istanbul climate normals", "https://www.mgm.gov.tr/veridegerlendirme/il-ve-ilceler-istatistik.aspx?m=ISTANBUL"),
    ("Windy (LTFM)", "https://www.windy.com/LTFM"),
    ("meteoblue MultiModel (LTFM)", "https://www.meteoblue.com/en/weather/forecast/multimodel/41.275N28.752E"),
]
