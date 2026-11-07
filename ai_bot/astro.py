"""Real Vedic (sidereal, Lahiri) birth chart with the Moshier ephemeris: no data files, no keys.
Place lookup and timezone come from the free Open-Meteo geocoding API."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import swisseph as swe

RASHI = ["Mesha (Aries)", "Vrishabha (Taurus)", "Mithuna (Gemini)", "Karka (Cancer)", "Simha (Leo)", "Kanya (Virgo)",
         "Tula (Libra)", "Vrishchika (Scorpio)", "Dhanu (Sagittarius)", "Makara (Capricorn)", "Kumbha (Aquarius)", "Meena (Pisces)"]
NAKSHATRA = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu", "Pushya", "Ashlesha", "Magha",
             "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula",
             "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"]
DASHA_LORDS = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
DASHA_YEARS = [7, 20, 6, 10, 7, 18, 16, 19, 17]
PLANETS = [("Sun", swe.SUN), ("Moon", swe.MOON), ("Mars", swe.MARS), ("Mercury", swe.MERCURY),
           ("Jupiter", swe.JUPITER), ("Venus", swe.VENUS), ("Saturn", swe.SATURN), ("Rahu", swe.MEAN_NODE)]
FLAGS = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED


class AstroError(Exception):
    pass


async def find_place(client: httpx.AsyncClient, place: str) -> dict:
    try:
        r = await client.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": place, "count": 1, "language": "en", "format": "json"}, timeout=15)
        res = (r.json().get("results") or [])
    except (httpx.HTTPError, ValueError):
        raise AstroError("place lookup is down right now")
    if not res:
        raise AstroError(f"could not find the place '{place}'")
    p = res[0]
    return {"name": f"{p.get('name')}, {p.get('admin1') or p.get('country') or ''}".strip(", "),
            "lat": p["latitude"], "lon": p["longitude"], "tz": p.get("timezone") or "Asia/Kolkata"}


def _sign(lon: float) -> int:
    return int(lon // 30) % 12


def _fmt(lon: float) -> str:
    return f"{RASHI[_sign(lon)]} {lon % 30:.1f}°"


def _nak(lon: float) -> tuple[str, int, int]:
    span = 360 / 27
    i = int(lon // span) % 27
    pada = int((lon % span) // (span / 4)) + 1
    return NAKSHATRA[i], pada, i


def compute(date: str, time_: str, place: dict) -> str:
    try:
        y, mo, d = (int(x) for x in date.split("-"))
        hh, mm = (int(x) for x in time_.split(":")[:2])
        local = dt.datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(place["tz"]))
    except (ValueError, KeyError):
        raise AstroError("date must be YYYY-MM-DD and time HH:MM")
    if not (1800 <= y <= 2100):
        raise AstroError("year out of range (1800-2100)")
    utc = local.astimezone(dt.timezone.utc)
    jd = swe.julday(utc.year, utc.month, utc.day, utc.hour + utc.minute / 60)
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    ayan = swe.get_ayanamsa_ut(jd)
    lons: dict[str, float] = {}
    lines = []
    for name, pid in PLANETS:
        xx, _ = swe.calc_ut(jd, pid, FLAGS)
        lons[name] = xx[0] % 360
        retro = " (retrograde)" if xx[3] < 0 and name not in ("Sun", "Moon", "Rahu") else ""
        lines.append((name, lons[name], retro))
    lons["Ketu"] = (lons["Rahu"] + 180) % 360
    lines.append(("Ketu", lons["Ketu"], ""))
    cusps, ascmc = swe.houses_ex(jd, place["lat"], place["lon"], b"W", FLAGS)
    asc = ascmc[0] % 360
    asc_sign = _sign(asc)
    out = [f"Birth chart (Vedic / sidereal, Lahiri ayanamsa {ayan:.2f}°)",
           f"Born: {local:%d %b %Y, %H:%M} ({place['tz']}) at {place['name']}",
           f"Lagna (ascendant): {_fmt(asc)}"]
    for name, lon, retro in lines:
        house = (_sign(lon) - asc_sign) % 12 + 1
        nk, pada, _ = _nak(lon)
        out.append(f"{name}: {_fmt(lon)}{retro}, house {house}, {nk} pada {pada}")
    moon = lons["Moon"]
    nk, pada, ni = _nak(moon)
    out.append(f"Moon sign (rashi): {RASHI[_sign(moon)]}; Janma nakshatra: {nk} pada {pada}")
    out.append(f"Sun sign (Vedic): {RASHI[_sign(lons['Sun'])]}")
    # Vimshottari dasha from the Moon's nakshatra
    span = 360 / 27
    frac_left = 1 - (moon % span) / span
    start_lord = ni % 9
    age_days = (dt.datetime.now(dt.timezone.utc) - utc).total_seconds() / 86400
    t = -DASHA_YEARS[start_lord] * (1 - frac_left) * 365.25
    k = start_lord
    now_md = None
    for _ in range(18):
        yrs = DASHA_YEARS[k % 9]
        end = t + yrs * 365.25
        if t <= age_days < end:
            now_md = (DASHA_LORDS[k % 9], t, end, k % 9)
            break
        t, k = end, k + 1
    if now_md:
        md, s, e, mi = now_md
        sub_t, ad = s, None
        for j in range(9):
            li = (mi + j) % 9
            ln = (e - s) * DASHA_YEARS[li] / 120
            if sub_t <= age_days < sub_t + ln:
                ad = (DASHA_LORDS[li], sub_t + ln)
                break
            sub_t += ln
        def dstr(days: float) -> str:
            return (utc + dt.timedelta(days=days)).strftime("%b %Y")
        out.append(f"Current Vimshottari dasha: {md} mahadasha until {dstr(e)}" + (f", {ad[0]} antardasha until {dstr(ad[1])}" if ad else ""))
    return "\n".join(out)


async def chart(client: httpx.AsyncClient, arg: str) -> str:
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) < 3 or not parts[2]:
        raise AstroError("need date | time | place")
    place = await find_place(client, parts[2])
    return compute(parts[0], parts[1], place)
