"""Safe link reading: http/https only, no private networks, size and time caps, scripts stripped."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
MAX_CHARS = 6000
UA = "Mozilla/5.0 (compatible; SathiBot/1.0)"


class UnsafeURL(Exception):
    pass


class FetchFailed(Exception):
    pass


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
            or a.is_reserved or a.is_unspecified)


async def check_url(url: str) -> str:
    """Raise UnsafeURL unless the URL is plain http(s) and every address it resolves to is public."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL("only http/https links")
    if p.username or p.password:
        raise UnsafeURL("no credentials in links")
    host = p.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                                              type=socket.SOCK_STREAM)
    except OSError as e:
        raise FetchFailed("host not found") from e
    for info in infos:
        if _bad_ip(info[4][0]):
            raise UnsafeURL("that address is not public")
    return url.strip()


_TAG_BLOCKS = re.compile(r"(?is)<(script|style|noscript|svg|iframe|template)\b.*?</\1>")
_COMMENTS = re.compile(r"(?s)<!--.*?-->")
_BREAKS = re.compile(r"(?i)</?(p|div|br|li|tr|h[1-6]|section|article|header|footer|ul|ol|table)\b[^>]*>")
_TAGS = re.compile(r"(?s)<[^>]+>")


def html_to_text(raw: str) -> tuple[str, str]:
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else ""
    t = _COMMENTS.sub(" ", raw)
    t = _TAG_BLOCKS.sub(" ", t)
    t = _BREAKS.sub("\n", t)
    t = _TAGS.sub(" ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return title, t.strip()


async def fetch_text(url: str, client: httpx.AsyncClient | None = None, max_chars: int = MAX_CHARS) -> str:
    """Return 'Title\nURL\n\ntext'. Redirects are followed by hand so each hop is checked."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": UA})
    try:
        cur = await check_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", cur, headers={"User-Agent": UA}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = await check_url(urljoin(cur, r.headers["location"]))
                    continue
                if r.status_code >= 400:
                    raise FetchFailed(f"site answered {r.status_code}")
                ctype = r.headers.get("content-type", "").lower()
                if ctype and not any(k in ctype for k in ("text/", "json", "xml")):
                    raise FetchFailed("that link is not a web page or text")
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        break
                enc = r.encoding or "utf-8"
                raw = bytes(data).decode(enc, errors="replace")
                if "html" in ctype or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                    title, body = html_to_text(raw)
                else:
                    title, body = "", raw.strip()
                head = (title + "\n" if title else "") + cur
                return f"{head}\n\n{body[:max_chars]}"
        raise FetchFailed("too many redirects")
    except httpx.HTTPError as e:
        raise FetchFailed("could not load the link") from e
    finally:
        if own:
            await client.aclose()
