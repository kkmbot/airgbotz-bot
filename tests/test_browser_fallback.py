import asyncio
from types import SimpleNamespace as NS

import httpx

from ai_bot import web
from ai_bot.core import Assistant


def test_browser_text(monkeypatch):
    async def ok(url):
        return url
    monkeypatch.setattr(web, "check_url", ok)
    html_ = "<html><title>T</title><body>" + "hello world " * 30 + "</body></html>"

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html_))) as c:
            return await web.fetch_via_browser("https://example.com", "tok", c)
    assert "hello world" in asyncio.run(go())


def test_login_walled_never_uses_browser():
    async def go():
        return await web.fetch_via_browser("https://www.instagram.com/x/", "tok", None)
    try:
        asyncio.run(go())
        assert False
    except web.FetchFailed as e:
        assert "screenshot" in str(e)
