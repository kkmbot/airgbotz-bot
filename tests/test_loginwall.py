import asyncio
import httpx
import pytest

from ai_bot import web


def test_instagram_429_asks_for_screenshot(monkeypatch):
    async def fake_check(url):
        return url
    monkeypatch.setattr(web, "check_url", fake_check)

    async def go(url):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as c:
            return await web.fetch_text(url, c)
    with pytest.raises(web.FetchFailed, match="screenshot"):
        asyncio.run(go("https://www.instagram.com/someone/"))
    with pytest.raises(web.FetchFailed, match="429"):
        asyncio.run(go("https://example.com/x"))
