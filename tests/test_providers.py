import json

import httpx
import pytest
from ai_bot.config import Settings
from ai_bot.providers import Cloudflare, Gemini, Groq, Ollama, OpenRouter, ProviderError, build_providers

SECRET = "AIza-TEST-SECRET-1234567890"


def client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_gemini_puts_key_in_header_not_url_and_parses_reply():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "namaste"}]}}]})

    g = Gemini(client_with(handler), SECRET, "gemini-flash-latest", [SECRET])
    out = await g.chat([{"role": "assistant", "content": "skip me"}, {"role": "user", "content": "hi"}], "SYS")
    assert out == "namaste"
    assert SECRET not in seen["url"]
    assert seen["key"] == SECRET
    assert seen["body"]["contents"][0]["role"] == "user"        # leading model turn dropped
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"


@pytest.mark.asyncio
async def test_gemini_vision_sends_inline_image():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "a dog"}]}}]})

    g = Gemini(client_with(handler), SECRET, "m")
    await g.chat([{"role": "user", "content": "what"}], "S", image=b"\xff\xd8", mime="image/jpeg")
    part = seen["body"]["contents"][-1]["parts"][-1]
    assert part["inline_data"]["mime_type"] == "image/jpeg"
    assert part["inline_data"]["data"] == "/9g="


@pytest.mark.asyncio
async def test_429_becomes_provider_error_with_retry_after_and_scrubbed_body():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "42"}, text=f"quota for key {SECRET}")

    g = Groq(client_with(handler), SECRET, "m", [SECRET])
    with pytest.raises(ProviderError) as ei:
        await g.chat([{"role": "user", "content": "x"}], "s")
    assert ei.value.status == 429
    assert ei.value.retry_after == 42.0
    assert SECRET not in ei.value.message


@pytest.mark.asyncio
async def test_timeout_becomes_status_none_error():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    o = OpenRouter(client_with(handler), SECRET, "openrouter/free")
    with pytest.raises(ProviderError) as ei:
        await o.chat([{"role": "user", "content": "x"}], "s")
    assert ei.value.status is None and ei.value.message == "timeout"


@pytest.mark.asyncio
async def test_groq_and_openrouter_use_bearer_and_system_first():
    seen = []

    def handler(req):
        seen.append((str(req.url), req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    c = client_with(handler)
    await Groq(c, SECRET, "llama").chat([{"role": "user", "content": "q"}], "SYS")
    await OpenRouter(c, SECRET, "openrouter/free").chat([{"role": "user", "content": "q"}], "SYS")
    assert seen[0][0].startswith("https://api.groq.com/openai/v1/")
    assert seen[1][0].startswith("https://openrouter.ai/api/v1/")
    for url, auth, body in seen:
        assert auth == f"Bearer {SECRET}"
        assert body["messages"][0] == {"role": "system", "content": "SYS"}


@pytest.mark.asyncio
async def test_cloudflare_and_ollama_parse_their_shapes():
    def handler(req):
        if "cloudflare" in str(req.url):
            return httpx.Response(200, json={"result": {"response": "cf says hi"}})
        return httpx.Response(200, json={"message": {"content": "local says hi"}})

    c = client_with(handler)
    assert await Cloudflare(c, "acct", SECRET, "@cf/x").chat([{"role": "user", "content": "q"}], "s") == "cf says hi"
    assert await Ollama(c, "http://ollama:11434", "qwen").chat([{"role": "user", "content": "q"}], "s") == "local says hi"


def test_unconfigured_adapters_report_not_configured():
    c = client_with(lambda r: httpx.Response(200))
    assert not Gemini(c, "", "m").configured()
    assert not Cloudflare(c, "acct", "", "m").configured()
    assert not Ollama(c, "", "m").configured()


def test_build_providers_follows_order_and_skips_unknown():
    s = Settings(provider_order=("groq", "nope", "ollama"), groq_api_key="k" * 12, ollama_url="http://o:11434")
    names = [p.name for p in build_providers(s, client_with(lambda r: httpx.Response(200)))]
    assert names == ["groq", "ollama"]
