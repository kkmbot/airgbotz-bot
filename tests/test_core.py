import pytest
from ai_bot import persona
from ai_bot.access import Access
from ai_bot.chain import Chain
from ai_bot.config import Settings
from ai_bot.core import Assistant, parse_tool
from ai_bot.store import Store
from ai_bot.tools import SearchClient, SearchUnavailable, Weather
from tests.fakes import FakeProvider, http_err

ADMIN = 111111111


class FakeSearch(SearchClient):
    def __init__(self, hits=None, fail=False):
        self.hits = hits or []
        self.fail = fail
        self.queries = []

    async def search(self, query, n=5):
        self.queries.append(query)
        if self.fail:
            raise SearchUnavailable()
        return self.hits


class FakeWeather(Weather):
    def __init__(self, text=None):
        self.text = text
        self.places = []

    async def current(self, place):
        self.places.append(place)
        return self.text


class FakeStt:
    def __init__(self, text="kya haal hai", ok=True):
        self.text = text
        self.ok = ok

    def configured(self):
        return self.ok

    async def transcribe(self, audio, filename, mime):
        return self.text


def build(providers, search=None, weather=None, stt=None, **settings):
    s = Settings(admin_ids=frozenset({ADMIN}), memory_turns=3, summary_every=2, daily_user_limit=2, **settings)
    store = Store(":memory:")
    chain = Chain(providers, store=store)
    access = Access(s, store)
    return Assistant(s, store, chain, access, search or FakeSearch(), weather or FakeWeather(), stt=stt), store


def test_parse_tool_recognises_only_short_tag_lines():
    assert parse_tool("SEARCH: bitcoin price today") == ("search", "bitcoin price today")
    assert parse_tool("weather: Jaipur") == ("weather", "Jaipur")
    assert parse_tool("Sure! SEARCH: x") is None
    assert parse_tool("SEARCH:") is None


@pytest.mark.asyncio
async def test_plain_answer_is_stored_and_counted():
    gem = FakeProvider("gemini", ["Hello! Kaise ho?"])
    a, store = build([gem])
    res = await a.answer_text(10, 42, "hi")
    assert res.kind == "ok" and res.provider == "gemini" and res.text == "Hello! Kaise ho?"
    assert [m["role"] for m in store.recent(10, 10)] == ["user", "assistant"]
    assert store.get_user_day(a.today(), 42) == 1


@pytest.mark.asyncio
async def test_search_tool_round_trip_uses_results_in_second_call():
    search = FakeSearch(hits=[{"title": "Rain news", "url": "https://n", "snippet": "rain tomorrow"}])
    gem = FakeProvider("gemini", ["SEARCH: mumbai rain tomorrow", "Kal baarish hogi (news site)."])
    a, _ = build([gem], search=search)
    res = await a.answer_text(10, 42, "kal mumbai me baarish hogi?")
    assert search.queries == ["mumbai rain tomorrow"]
    assert "rain tomorrow" in gem.last_messages[-1]["content"]
    assert res.text == "Kal baarish hogi (news site)."


@pytest.mark.asyncio
async def test_search_down_still_answers_with_note():
    search = FakeSearch(fail=True)
    gem = FakeProvider("gemini", ["SEARCH: x", "Mujhe pakki info nahi mili, but ..."])
    a, _ = build([gem], search=search)
    res = await a.answer_text(10, 42, "latest news?")
    assert res.kind == "ok"
    assert "search service unavailable" in gem.last_messages[-1]["content"]


@pytest.mark.asyncio
async def test_weather_tool_called_for_weather_question():
    w = FakeWeather("Delhi, India: 30°C, saaf aasmaan")
    gem = FakeProvider("gemini", ["WEATHER: Delhi", "Delhi me 30 degree hai, dhoop hai."])
    a, _ = build([gem], weather=w)
    await a.answer_text(10, 42, "delhi ka mausam")
    assert w.places == ["Delhi"]


@pytest.mark.asyncio
async def test_all_providers_down_gives_polite_line_and_no_crash():
    a, store = build([FakeProvider("gemini", [http_err("gemini", 429)])])
    res = await a.answer_text(10, 42, "hi")
    assert res.kind == "down" and res.text == persona.DOWN_TEXT


@pytest.mark.asyncio
async def test_daily_cap_applies_to_users_not_admin():
    gem = FakeProvider("gemini", ["a", "b", "c", "d", "e"])
    a, _ = build([gem])
    assert (await a.answer_text(10, 42, "1")).kind == "ok"
    assert (await a.answer_text(10, 42, "2")).kind == "ok"
    assert (await a.answer_text(10, 42, "3")).kind == "limit"
    for _ in range(3):
        assert (await a.answer_text(10, ADMIN, "x")).kind == "ok"


@pytest.mark.asyncio
async def test_summary_folds_old_messages_and_keeps_window():
    gem = FakeProvider("gemini", ["r1", "r2", "r3", "r4", "r5", "SUMMARY: user likes tea"] + ["r"] * 20)
    a, store = build([gem])
    s = a.settings
    for i in range(6):
        await a.answer_text(10, ADMIN, f"q{i}")
    assert store.count_messages(10) <= a._window() + s.summary_every
    assert store.get_summary(10)  # something was folded in


@pytest.mark.asyncio
async def test_photo_uses_vision_provider_only():
    text_only = FakeProvider("groq", ["never"])
    eyes = FakeProvider("gemini", ["Ek billi baithi hai."], vision=True)
    a, store = build([text_only, eyes])
    res = await a.answer_photo(10, 42, b"img", "image/jpeg", "ye kya hai")
    assert res.text == "Ek billi baithi hai." and text_only.calls == 0
    assert store.recent(10, 1)[0]["content"] == "Ek billi baithi hai."


@pytest.mark.asyncio
async def test_photo_without_vision_is_polite():
    a, _ = build([FakeProvider("groq", ["x"])])
    res = await a.answer_photo(10, 42, b"img", "image/jpeg", "")
    assert res.kind == "off" and res.text == persona.PHOTO_DOWN_TEXT


@pytest.mark.asyncio
async def test_voice_off_when_no_stt_key():
    a, _ = build([FakeProvider("gemini", ["x"])], stt=FakeStt(ok=False))
    res = await a.answer_voice(10, 42, b"ogg", "voice.ogg", "audio/ogg")
    assert res.kind == "off" and res.text == persona.VOICE_OFF_TEXT


@pytest.mark.asyncio
async def test_voice_transcribed_then_answered():
    gem = FakeProvider("gemini", ["Badhiya, main theek hoon!"])
    a, _ = build([gem], stt=FakeStt("kaisa hai bhai"))
    res = await a.answer_voice(10, 42, b"ogg", "voice.ogg", "audio/ogg")
    assert "kaisa hai bhai" in res.text and "Badhiya" in res.text


def test_reset_and_status_and_usage_texts():
    gem = FakeProvider("gemini", ["x"])
    a, store = build([gem, FakeProvider("groq", configured=False)])
    store.add_message(10, "user", "hi")
    assert "clear" in a.reset(10)
    assert store.count_messages(10) == 0
    assert "groq: off" in a.model_status()
    assert "Aaj abhi koi call nahi hui" in a.usage_text()
