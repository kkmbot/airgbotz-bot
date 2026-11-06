import asyncio

import httpx

from ai_bot import extras


def test_qr_parse_and_png():
    assert extras.qr_request("qr code for https://example.com") == "https://example.com"
    assert extras.qr_request("my website ka qr banao") == "my website"
    assert extras.qr_request("quarterly report please") is None
    assert extras.make_qr("hello")[:4] == b"\x89PNG"


def test_money_parse():
    assert extras.money_request("100 usd to inr") == ("fx", 100.0, "usd", "inr")
    assert extras.money_request("50 dollars in rupees") == ("fx", 50.0, "usd", "inr")
    assert extras.money_request("bitcoin price") == ("crypto", "bitcoin")
    assert extras.money_request("how are you") is None
    assert extras.money_request("5 apples to oranges") is None


def test_money_answer_mocked():
    def h(req):
        return httpx.Response(200, json={"date": "2026-11-05", "rates": {"INR": 9448.0}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await extras.money_answer(c, ("fx", 100.0, "usd", "inr"))
    assert "9,448.00 INR" in asyncio.run(go())


def test_reminder_parse():
    assert extras.reminder_request("remind me in 10 minutes to drink water") == (600, "drink water")
    assert extras.reminder_request("2 ghante baad yaad dilana dawai lena") == (7200, "dawai lena")
    assert extras.reminder_request("remind me in 99 years to die") is None
    assert extras.reminder_request("what is a reminder") is None
