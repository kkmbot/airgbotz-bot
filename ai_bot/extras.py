"""Small keyless helpers that need no AI: QR codes, currency and crypto prices, reminder parsing."""
from __future__ import annotations

import io
import re

import httpx

CRYPTO = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "dogecoin": "dogecoin",
          "doge": "dogecoin", "solana": "solana", "sol": "solana", "xrp": "ripple", "bnb": "binancecoin"}
CUR = {"usd", "inr", "eur", "gbp", "aed", "jpy", "cad", "aud", "sgd", "cny", "chf", "sar", "pkr", "bdt", "lkr", "npr"}
_NAMES = {"dollar": "usd", "dollars": "usd", "rupee": "inr", "rupees": "inr", "rupaye": "inr", "euro": "eur", "euros": "eur",
          "pound": "gbp", "pounds": "gbp", "dirham": "aed", "yen": "jpy"}


def make_qr(text: str) -> bytes:
    import segno
    buf = io.BytesIO()
    segno.make(text[:900], error="m").save(buf, kind="png", scale=10, border=3)
    return buf.getvalue()


def qr_request(text: str) -> str | None:
    m = re.match(r"(?is)^\s*(?:please\s+)?(?:make|create|generate|banao|bana do|bana de)?\s*(?:a\s+)?qr(?:\s*code)?\s*(?:for|of|banao|bana do|bana de|:|-)?\s*(.+)$", text)
    if m and len(m.group(1).strip()) >= 2 and len(text) < 1000:
        return m.group(1).strip()
    m = re.match(r"(?is)^\s*(.+?)\s+(?:ka|ki)\s+qr(?:\s*code)?\s*(?:banao|bana do|bana de)?\s*$", text)
    return m.group(1).strip() if m else None


def _cur(word: str) -> str | None:
    w = word.lower().strip(".")
    w = _NAMES.get(w, w)
    return w if w in CUR else None


def money_request(text: str):
    """'100 usd to inr', '50 dollar in rupees', 'bitcoin price' -> ('fx', amount, a, b) | ('crypto', coin) | None"""
    t = text.strip().lower()
    m = re.fullmatch(r"(?:convert\s+)?([\d,]+(?:\.\d+)?)\s*([a-z]+)\s*(?:to|in|into|me|mein)\s*([a-z]+)\??", t)
    if m:
        a, b = _cur(m.group(2)), _cur(m.group(3))
        if a and b:
            return ("fx", float(m.group(1).replace(",", "")), a, b)
    m = re.fullmatch(r"(?:what(?:'s| is) (?:the )?)?(?:price of |current )?([a-z]+)(?: price| ka price| ka rate| rate| kitne ka hai)?\??", t)
    if m and m.group(1) in CRYPTO and (len(t) > len(m.group(1)) or t in ("bitcoin", "ethereum")):
        return ("crypto", CRYPTO[m.group(1)])
    return None


async def money_answer(client: httpx.AsyncClient, req) -> str:
    try:
        if req[0] == "fx":
            _, amt, a, b = req
            r = await client.get("https://api.frankfurter.dev/v1/latest", params={"base": a.upper(), "symbols": b.upper(), "amount": amt}, timeout=15)
            r.raise_for_status()
            d = r.json()
            return f"{amt:,.2f} {a.upper()} = {d['rates'][b.upper()]:,.2f} {b.upper()} (rate of {d['date']}, ECB reference)"
        coin = req[1]
        r = await client.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": coin, "vs_currencies": "inr,usd"}, timeout=15)
        r.raise_for_status()
        d = r.json()[coin]
        return f"{coin.title()}: ${d['usd']:,.2f} / ₹{d['inr']:,.0f} (live from CoinGecko)"
    except Exception:  # noqa: BLE001
        return "Couldn't get the rate right now, try again in a minute 🙏"


_UNIT = {"s": 1, "sec": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minat": 60,
         "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "ghanta": 3600, "ghante": 3600, "ghanta": 3600, "d": 86400, "day": 86400, "days": 86400, "din": 86400}


def reminder_request(text: str):
    """'remind me in 10 minutes to drink water' / '10 minute baad yaad dilana pani peena' -> (seconds, what) or None"""
    t = text.strip()
    m = re.match(r"(?is)^\s*(?:please\s+)?remind me\s+in\s+(\d+)\s*([a-z]+)\s*(?:to|that|about)?\s*(.*)$", t)
    if not m:
        m = re.match(r"(?is)^\s*(\d+)\s*([a-z]+)\s*(?:baad|bad|mein|me)\s*(?:mujhe\s*)?(?:yaad\s*dila(?:na|o|do)?|remind(?:er)?)\s*(.*)$", t)
    if not m:
        return None
    n, unit = int(m.group(1)), _UNIT.get(m.group(2).lower())
    what = (m.group(3) or "").strip() or "your reminder"
    if not unit or n <= 0:
        return None
    secs = n * unit
    if secs > 7 * 86400:
        return None
    return secs, what[:300]
