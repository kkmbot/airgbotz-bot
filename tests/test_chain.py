import pytest
from ai_bot.chain import AllProvidersDown, Chain
from ai_bot.store import Store
from tests.fakes import FakeClock, FakeProvider, http_err

MSG = [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_first_provider_answers_and_is_recorded():
    store = Store(":memory:")
    a = FakeProvider("a", ["from a"])
    b = FakeProvider("b", ["from b"])
    chain = Chain([a, b], store=store)
    text, who = await chain.generate(MSG, "sys")
    assert (text, who) == ("from a", "a")
    assert b.calls == 0
    assert chain.last_provider == "a"
    assert store.usage_for_day(chain.day_fn())[0]["count"] == 1


@pytest.mark.asyncio
async def test_falls_through_on_5xx_and_timeout_without_cooldown():
    a = FakeProvider("a", [http_err("a", 503)])
    b = FakeProvider("b", [http_err("b", None)])  # timeout / network style
    c = FakeProvider("c", ["from c"])
    clock = FakeClock()
    chain = Chain([a, b, c], clock=clock)
    assert (await chain.generate(MSG, "s"))[1] == "c"
    # no cooldown for plain errors: a is tried again next time
    a.script = ["a is back"]
    assert (await chain.generate(MSG, "s"))[1] == "a"


@pytest.mark.asyncio
async def test_429_puts_provider_on_cooldown_then_releases():
    clock = FakeClock()
    a = FakeProvider("a", [http_err("a", 429), "a again"])
    b = FakeProvider("b", ["b1", "b2", "b3"])
    chain = Chain([a, b], cooldown_seconds=600, clock=clock)
    assert (await chain.generate(MSG, "s"))[1] == "b"   # a hit 429
    assert chain.cooldown_left("a") == 600
    assert (await chain.generate(MSG, "s"))[1] == "b"   # a skipped while cooling
    assert a.calls == 1
    clock.t += 601
    assert (await chain.generate(MSG, "s"))[1] == "a"   # cooldown over


@pytest.mark.asyncio
async def test_retry_after_longer_than_default_wins_but_capped_at_an_hour():
    clock = FakeClock()
    a = FakeProvider("a", [http_err("a", 429, retry_after=7200)])
    b = FakeProvider("b", ["b"])
    chain = Chain([a, b], cooldown_seconds=600, clock=clock)
    await chain.generate(MSG, "s")
    assert chain.cooldown_left("a") == 3600


@pytest.mark.asyncio
async def test_auth_refusal_puts_provider_off_for_long():
    clock = FakeClock()
    a = FakeProvider("a", [http_err("a", 401)])
    b = FakeProvider("b", ["b"])
    chain = Chain([a, b], auth_cooldown_seconds=3600, clock=clock)
    await chain.generate(MSG, "s")
    assert chain.cooldown_left("a") == 3600


@pytest.mark.asyncio
async def test_unconfigured_provider_is_skipped_silently():
    a = FakeProvider("a", configured=False)
    b = FakeProvider("b", ["b"])
    chain = Chain([a, b])
    assert (await chain.generate(MSG, "s"))[1] == "b"
    assert a.calls == 0


@pytest.mark.asyncio
async def test_all_down_raises_all_providers_down():
    chain = Chain([FakeProvider("a", [http_err("a", 500)]), FakeProvider("b", [RuntimeError("boom")])])
    with pytest.raises(AllProvidersDown):
        await chain.generate(MSG, "s")


@pytest.mark.asyncio
async def test_empty_reply_counts_as_failure():
    a = FakeProvider("a", ["   "])
    b = FakeProvider("b", ["real"])
    chain = Chain([a, b])
    assert (await chain.generate(MSG, "s"))[0] == "real"


@pytest.mark.asyncio
async def test_vision_only_uses_vision_providers():
    text_only = FakeProvider("groq", ["should not be used"])
    eyes = FakeProvider("gemini", ["a cat"], vision=True)
    chain = Chain([text_only, eyes])
    assert (await chain.generate(MSG, "s", image=b"x", mime="image/jpeg", vision=True)) == ("a cat", "gemini")
    assert text_only.calls == 0


@pytest.mark.asyncio
async def test_vision_with_no_vision_provider_is_all_down():
    chain = Chain([FakeProvider("groq", ["x"])])
    with pytest.raises(AllProvidersDown):
        await chain.generate(MSG, "s", image=b"x", vision=True)


def test_status_lists_each_provider_state():
    clock = FakeClock()
    a = FakeProvider("a", configured=False)
    b = FakeProvider("b")
    chain = Chain([a, b], clock=clock)
    chain._start_cooldown("b", 30)
    st = dict(chain.status())
    assert st["a"].startswith("off")
    assert st["b"].startswith("cooling down")
