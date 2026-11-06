"""Web search (Tavily, then Serper) and weather (Open-Meteo, no key)."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class SearchUnavailable(Exception):
    pass


class WeatherUnavailable(Exception):
    pass


class SearchClient:
    def __init__(self, client: httpx.AsyncClient, tavily_key: str = "", serper_key: str = "") -> None:
        self.client = client
        self.tavily_key = tavily_key
        self.serper_key = serper_key

    def configured(self) -> bool:
        return bool(self.tavily_key or self.serper_key)

    async def search(self, query: str, n: int = 5) -> list[dict]:
        """Tavily first; on any failure (limit, 5xx, timeout) Serper; if both fail, SearchUnavailable."""
        query = query.strip()[:300]
        if self.tavily_key:
            try:
                return await self._tavily(query, n)
            except Exception as e:  # noqa: BLE001 - any failure moves to the fallback
                log.warning("tavily search failed (%s); trying serper", type(e).__name__)
        if self.serper_key:
            try:
                return await self._serper(query, n)
            except Exception as e:  # noqa: BLE001
                log.warning("serper search failed (%s)", type(e).__name__)
        raise SearchUnavailable("no search provider answered")

    async def _tavily(self, query: str, n: int) -> list[dict]:
        r = await self.client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.tavily_key}"},
            json={"query": query, "max_results": n, "search_depth": "basic"}, timeout=20)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                for x in r.json().get("results", [])]

    async def _serper(self, query: str, n: int) -> list[dict]:
        r = await self.client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.serper_key, "Content-Type": "application/json"},
            json={"q": query, "num": n}, timeout=20)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in r.json().get("organic", [])]


WMO = {0: "saaf aasmaan", 1: "thoda saaf", 2: "aadha baadal", 3: "baadal",
       45: "kohra", 48: "kohra", 51: "halki boondabaandi", 53: "boondabaandi", 55: "tez boondabaandi",
       61: "halki baarish", 63: "baarish", 65: "tez baarish", 66: "jamne wali baarish", 67: "tez jamne wali baarish",
       71: "halki barf", 73: "barf", 75: "tez barf", 77: "barf ke daane",
       80: "halki bauchhar", 81: "bauchhar", 82: "tez bauchhar", 85: "barf ki bauchhar", 86: "tez barf ki bauchhar",
       95: "garaj ke saath baarish", 96: "olon ke saath garaj", 99: "olon ke saath garaj"}


class Weather:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def current(self, place: str) -> str | None:
        """Returns a one-line summary, or None when the place is not found."""
        place = place.strip()[:80]
        try:
            g = await self.client.get("https://geocoding-api.open-meteo.com/v1/search",
                                      params={"name": place, "count": 1, "language": "en", "format": "json"},
                                      timeout=15)
            g.raise_for_status()
            hits = g.json().get("results") or []
            if not hits:
                return None
            loc = hits[0]
            f = await self.client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": loc["latitude"], "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "timezone": "auto"}, timeout=15)
            f.raise_for_status()
            cur = f.json().get("current") or {}
        except Exception as e:  # noqa: BLE001
            log.warning("weather lookup failed (%s)", type(e).__name__)
            raise WeatherUnavailable() from e
        code = int(cur.get("weather_code", -1))
        return (f"{loc.get('name', place)}, {loc.get('country', '')}: {cur.get('temperature_2m')}°C, "
                f"{WMO.get(code, 'mausam ka code ' + str(code))}, nami {cur.get('relative_humidity_2m')}%, "
                f"hawa {cur.get('wind_speed_10m')} km/h (Open-Meteo)").replace(", ,", ",")
