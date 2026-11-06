import httpx
import pytest
from ai_bot.tools import SearchClient, SearchUnavailable, Weather, WeatherUnavailable


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_tavily_first_then_serper_on_failure():
    calls = []

    def handler(req):
        calls.append(req.url.host)
        if req.url.host == "api.tavily.com":
            return httpx.Response(429, text="limit")
        return httpx.Response(200, json={"organic": [{"title": "T", "link": "https://x", "snippet": "S"}]})

    sc = SearchClient(client(handler), "tv-key-123456", "sp-key-123456")
    hits = await sc.search("news")
    assert calls == ["api.tavily.com", "google.serper.dev"]
    assert hits == [{"title": "T", "url": "https://x", "snippet": "S"}]


@pytest.mark.asyncio
async def test_tavily_result_shape():
    sc = SearchClient(client(lambda r: httpx.Response(200, json={"results": [
        {"title": "A", "url": "https://a", "content": "c"}]})), "tv-key-123456")
    assert (await sc.search("q"))[0]["snippet"] == "c"


@pytest.mark.asyncio
async def test_no_search_keys_is_unavailable():
    sc = SearchClient(client(lambda r: httpx.Response(200)))
    with pytest.raises(SearchUnavailable):
        await sc.search("q")


@pytest.mark.asyncio
async def test_both_search_providers_down_raises():
    sc = SearchClient(client(lambda r: httpx.Response(500)), "tv-key-123456", "sp-key-123456")
    with pytest.raises(SearchUnavailable):
        await sc.search("q")


@pytest.mark.asyncio
async def test_weather_summary_and_unknown_place():
    def handler(req):
        if "geocoding" in req.url.host:
            if req.url.params["name"] == "Nowhereville":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"results": [
                {"name": "Delhi", "country": "India", "latitude": 28.6, "longitude": 77.2}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 21.5, "relative_humidity_2m": 40,
                                                     "weather_code": 0, "wind_speed_10m": 9}})

    w = Weather(client(handler))
    line = await w.current("Delhi")
    assert "21.5°C" in line and "saaf aasmaan" in line and "Open-Meteo" in line
    assert await w.current("Nowhereville") is None


@pytest.mark.asyncio
async def test_weather_service_down_raises():
    w = Weather(client(lambda r: httpx.Response(503)))
    with pytest.raises(WeatherUnavailable):
        await w.current("Delhi")
