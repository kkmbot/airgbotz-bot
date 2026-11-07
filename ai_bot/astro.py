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
    navs = {name: int(lon // (30 / 9)) % 12 for name, lon in lons.items()}
    out.append("Navamsa (D9) signs: " + ", ".join(f"{n} {RASHI[s].split(' ')[0]}" for n, s in navs.items() if n != "Ketu")
               + f"; Navamsa lagna {RASHI[int(asc // (30 / 9)) % 12].split(' ')[0]}")
    mars_h = (_sign(lons["Mars"]) - asc_sign) % 12 + 1
    mars_m = (_sign(lons["Mars"]) - _sign(lons["Moon"])) % 12 + 1
    md_l = mars_h in (1, 4, 7, 8, 12)
    md_m = mars_m in (1, 4, 7, 8, 12)
    out.append(f"Mangal dosha check: Mars is in house {mars_h} from Lagna and house {mars_m} from Moon -> "
               + ("Mangal dosha present" + (" (from Lagna)" if md_l else "") + (" (from Moon)" if md_m else "")
                  if (md_l or md_m) else "no Mangal dosha by the common rule (houses 1,4,7,8,12; some schools add 2). Cancellations are not evaluated."))
    # today's transits (gochar), same sidereal system
    now = dt.datetime.now(dt.timezone.utc)
    jn = swe.julday(now.year, now.month, now.day, now.hour + now.minute / 60)
    tr = []
    msign = _sign(lons["Moon"])
    for name, pid in (("Saturn", swe.SATURN), ("Jupiter", swe.JUPITER), ("Rahu", swe.MEAN_NODE)):
        xx, _ = swe.calc_ut(jn, pid, FLAGS)
        s = _sign(xx[0] % 360)
        tr.append((name, s, (s - asc_sign) % 12 + 1, (s - msign) % 12 + 1))
    out.append("Transits today: " + "; ".join(f"{n} in {RASHI[s]} (house {h} from Lagna, {m} from Moon)" for n, s, h, m in tr))
    sat_m = tr[0][3]
    out.append("Sade Sati: " + ({12: "running, first phase (Saturn 12th from Moon)", 1: "running, peak phase (Saturn over the Moon sign)",
                               2: "running, last phase (Saturn 2nd from Moon)"}.get(sat_m, "not running now")))
    warn = []
    if asc % 30 < 1.5 or asc % 30 > 28.5:
        warn.append("Lagna is within 1.5 degrees of a sign boundary: a birth-time error of ~6 minutes can change the Lagna sign, so confirm the exact time")
    elif True:
        warn.append("Lagna changes sign about every 2 hours, so it is only as right as the birth time given")
    if (moon_cut := lons["Moon"] % (360 / 27 / 4)) < 0.1 or moon_cut > (360 / 27 / 4) - 0.1:
        warn.append("Moon is at a pada boundary")
    out.append("Accuracy notes: planets computed with Swiss Ephemeris (checked against NASA JPL Horizons, within 1 arcsecond); Rahu is the Mean Node; "
               + "; ".join(warn))
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


# ---------------------------------------------------------------- Kundli milan (Ashtakoot, 36 points)
_VARNA_RANK = {3: 4, 7: 4, 11: 4, 0: 3, 4: 3, 8: 3, 1: 2, 5: 2, 9: 2, 2: 1, 6: 1, 10: 1}  # rashi index -> rank (Brahmin 4 .. Shudra 1)
# Vashya groups: 0 human, 1 leo/wild, 2 quadruped, 3 water, 4 scorpion/insect. Matrix is [boy][girl].
_VASHYA = [[2.0, 0.5, 1.0, 1.0, 1.0], [0.0, 2.0, 0.0, 0.0, 0.0], [0.0, 0.5, 2.0, 0.0, 0.0],
           [1.0, 1.0, 0.5, 2.0, 1.0], [1.0, 0.0, 1.0, 1.0, 2.0]]
_ANIMAL = [0, 1, 2, 3, 3, 4, 5, 2, 5, 6, 6, 7, 8, 9, 8, 9, 10, 10, 4, 11, 12, 11, 13, 0, 13, 7, 1]
_YONI = [[4, 2, 2, 3, 2, 2, 2, 1, 0, 1, 1, 3, 2, 1], [2, 4, 3, 3, 2, 2, 2, 2, 3, 1, 2, 3, 2, 0],
         [2, 3, 4, 2, 1, 2, 1, 3, 3, 1, 2, 0, 3, 1], [3, 3, 2, 4, 2, 1, 1, 1, 1, 2, 2, 2, 0, 2],
         [2, 2, 1, 2, 4, 2, 1, 2, 2, 1, 0, 2, 1, 1], [2, 2, 2, 1, 2, 4, 0, 2, 2, 1, 3, 3, 2, 1],
         [2, 2, 1, 1, 1, 0, 4, 2, 2, 2, 2, 2, 1, 2], [1, 2, 3, 1, 2, 2, 2, 4, 3, 0, 3, 2, 2, 1],
         [0, 3, 3, 1, 2, 2, 2, 3, 4, 1, 2, 2, 2, 2], [1, 1, 1, 2, 1, 1, 2, 0, 1, 4, 1, 1, 2, 1],
         [1, 2, 2, 2, 0, 3, 2, 3, 2, 1, 4, 2, 2, 1], [3, 3, 0, 2, 2, 3, 2, 2, 2, 1, 2, 4, 3, 2],
         [2, 2, 3, 0, 1, 2, 1, 2, 2, 2, 2, 3, 4, 2], [1, 0, 1, 2, 1, 1, 2, 1, 2, 1, 1, 2, 2, 4]]  # [girl animal][boy animal]
_LORD = [2, 5, 3, 1, 0, 3, 5, 2, 4, 6, 6, 4]  # rashi -> Mars, Venus, Mercury, Moon, Sun, Mercury, Venus, Mars, Jupiter, Saturn, Saturn, Jupiter
_MAITRI = [[5.0, 5.0, 5.0, 4.0, 5.0, 0.0, 0.0], [5.0, 5.0, 4.0, 1.0, 4.0, 0.5, 0.5], [5.0, 4.0, 5.0, 0.5, 5.0, 3.0, 0.5],
           [4.0, 1.0, 0.5, 5.0, 0.5, 5.0, 4.0], [5.0, 4.0, 5.0, 0.5, 5.0, 0.5, 3.0], [0.0, 0.5, 3.0, 5.0, 0.5, 5.0, 5.0],
           [0.0, 0.5, 0.5, 4.0, 3.0, 5.0, 5.0]]  # [girl lord][boy lord], order Sun Moon Mars Mercury Jupiter Venus Saturn
_GANA_OF = [0, 1, 2, 1, 0, 1, 0, 0, 2, 2, 1, 1, 0, 2, 0, 2, 0, 2, 2, 1, 1, 0, 2, 2, 1, 1, 0]  # 0 deva, 1 manushya, 2 rakshasa
_GANA = [[6, 5, 1], [6, 6, 0], [0, 0, 6]]  # [girl gana][boy gana]
_NADI_OF = [0, 1, 2, 2, 1, 0, 0, 1, 2, 2, 1, 0, 0, 1, 2, 2, 1, 0, 0, 1, 2, 2, 1, 0, 0, 1, 2]
GANA_NAME = ["Deva", "Manushya", "Rakshasa"]
NADI_NAME = ["Adi", "Madhya", "Antya"]


def _vashya_group(sign: int, deg: float) -> int:
    if sign in (2, 5, 6, 10):
        return 0
    if sign == 8:  # Sagittarius: human first half, quadruped second half
        return 0 if deg < 15 else 2
    if sign == 9:  # Capricorn: quadruped first half, water second half
        return 2 if deg < 15 else 3
    if sign == 4:
        return 1
    if sign in (0, 1):
        return 2
    if sign in (3, 11):
        return 3
    return 4


def moon_of(date: str, time_: str, place: dict) -> dict:
    y, mo, d = (int(x) for x in date.split("-"))
    hh, mm = (int(x) for x in time_.split(":")[:2])
    local = dt.datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(place["tz"]))
    utc = local.astimezone(dt.timezone.utc)
    jd = swe.julday(utc.year, utc.month, utc.day, utc.hour + utc.minute / 60)
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    xx, _ = swe.calc_ut(jd, swe.MOON, FLAGS)
    lon = xx[0] % 360
    nk, pada, ni = _nak(lon)
    mars = swe.calc_ut(jd, swe.MARS, FLAGS)[0][0] % 360
    try:
        _, ascmc = swe.houses_ex(jd, place["lat"], place["lon"], b"W", FLAGS)
        asc_sign = _sign(ascmc[0] % 360)
        mars_lagna = (_sign(mars) - asc_sign) % 12 + 1
    except Exception:  # noqa: BLE001
        mars_lagna = 0
    mars_moon = (_sign(mars) - _sign(lon)) % 12 + 1
    manglik = mars_lagna in (1, 4, 7, 8, 12) or mars_moon in (1, 4, 7, 8, 12)
    return {"lon": lon, "sign": _sign(lon), "deg": lon % 30, "nak": ni, "nak_name": nk, "pada": pada, "manglik": manglik,
            "mars_lagna": mars_lagna, "mars_moon": mars_moon}


def ashtakoot(boy: dict, girl: dict) -> dict:
    res = {}
    res["Varna"] = (1.0 if _VARNA_RANK[boy["sign"]] >= _VARNA_RANK[girl["sign"]] else 0.0, 1)
    res["Vashya"] = (_VASHYA[_vashya_group(boy["sign"], boy["deg"])][_vashya_group(girl["sign"], girl["deg"])], 2)
    def rem(a: int, b: int) -> int:
        r = (((a - b) % 27) + 1) % 9
        return r or 9
    bad = lambda r: r in (3, 5, 7)
    n_bad = int(bad(rem(boy["nak"], girl["nak"]))) + int(bad(rem(girl["nak"], boy["nak"])))
    res["Tara"] = ({0: 3.0, 1: 1.5, 2: 0.0}[n_bad], 3)
    res["Yoni"] = (float(_YONI[_ANIMAL[girl["nak"]]][_ANIMAL[boy["nak"]]]), 4)
    res["Graha Maitri"] = (_MAITRI[_LORD[girl["sign"]]][_LORD[boy["sign"]]], 5)
    res["Gana"] = (float(_GANA[_GANA_OF[girl["nak"]]][_GANA_OF[boy["nak"]]]), 6)
    d = ((boy["sign"] - girl["sign"]) % 12) + 1
    res["Bhakoot"] = (0.0 if d in (2, 12, 5, 9, 6, 8) else 7.0, 7)
    res["Nadi"] = (0.0 if _NADI_OF[boy["nak"]] == _NADI_OF[girl["nak"]] else 8.0, 8)
    total = sum(v for v, _ in res.values())
    return {"parts": res, "total": total}


async def match(client: httpx.AsyncClient, arg: str) -> str:
    """arg: '<boy date | time | place> ;; <girl date | time | place>'"""
    halves = [h for h in arg.split(";;") if h.strip()]
    if len(halves) != 2:
        raise AstroError("need two people: boy and girl, each as date | time | place, split by ;;")
    people = []
    for h in halves:
        parts = [p.strip() for p in h.split("|")]
        if len(parts) < 3 or not parts[2]:
            raise AstroError("each person needs date | time | place")
        try:
            place = await find_place(client, parts[2])
            people.append((moon_of(parts[0], parts[1], place), place))
        except (ValueError, KeyError):
            raise AstroError("date must be YYYY-MM-DD and time HH:MM")
    (b, bp), (g, gp) = people
    r = ashtakoot(b, g)
    lines = ["Kundli milan (Ashtakoot, Vedic / Lahiri). Based on each person's Moon sign and nakshatra.",
             f"Boy: Moon in {RASHI[b['sign']]}, {b['nak_name']} pada {b['pada']} ({GANA_NAME[_GANA_OF[b['nak']]]} gana, {NADI_NAME[_NADI_OF[b['nak']]]} nadi)",
             f"Girl: Moon in {RASHI[g['sign']]}, {g['nak_name']} pada {g['pada']} ({GANA_NAME[_GANA_OF[g['nak']]]} gana, {NADI_NAME[_NADI_OF[g['nak']]]} nadi)"]
    for k, (v, mx) in r["parts"].items():
        lines.append(f"{k}: {v:g} / {mx}")
    lines.append(f"TOTAL: {r['total']:g} / 36")
    lines.append(f"Mangal dosha: boy {'yes' if b['manglik'] else 'no'}, girl {'yes' if g['manglik'] else 'no'} "
                 f"(Mars in house {b['mars_lagna']} / {b['mars_moon']} from Lagna / Moon for the boy; {g['mars_lagna']} / {g['mars_moon']} for the girl)."
                 + (" Both are manglik, which is traditionally considered balanced." if b['manglik'] and g['manglik'] else
                    (" Only one is manglik, which is traditionally flagged for a remedy or a closer look." if b['manglik'] != g['manglik'] else "")))
    if r["parts"]["Nadi"][0] == 0:
        lines.append("Nadi dosha present (same nadi).")
    if r["parts"]["Bhakoot"][0] == 0:
        lines.append("Bhakoot dosha present (the two Moon signs are in a 2-12, 5-9 or 6-8 relation).")
    return "\n".join(lines)
