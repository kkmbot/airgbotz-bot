"""Drive the real aiogram dispatcher with fake updates. The Telegram HTTP session is replaced by a recorder,
so nothing leaves the machine and nothing is sent to anyone."""
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Update

from ai_bot import persona
from ai_bot.access import Access
from ai_bot.chain import Chain
from ai_bot.config import Settings
from ai_bot.core import Assistant
from ai_bot.store import Store
import importlib

import ai_bot.telegram_app as tg
from ai_bot.tools import SearchClient, Weather
from tests.fakes import FakeProvider

ADMIN = 111111111
OTHER = 4242
BOT_ID = 999


class RecorderSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, dict]] = []

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        params = method.model_dump(exclude_none=True)
        self.sent.append((type(method).__name__, params))
        if type(method).__name__ == "SendMessage":
            return {"message_id": len(self.sent), "date": 0, "chat": {"id": params["chat_id"], "type": "private"},
                    "text": params.get("text", "")}
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        raise NotImplementedError


def make_update(uid, text, update_id=1, chat_type="private"):
    chat_id = uid
    return Update.model_validate({
        "update_id": update_id,
        "message": {"message_id": update_id, "date": 0,
                    "chat": {"id": chat_id, "type": chat_type},
                    "from": {"id": uid, "is_bot": False, "first_name": "T"},
                    "text": text,
                    "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
                    if text.startswith("/") else []},
    })


def setup(replies=("Hello from the model!",)):
    # aiogram lets a router belong to one dispatcher only; reload the module for a fresh router per test
    tg = importlib.reload(importlib.import_module("ai_bot.telegram_app"))
    settings = Settings(admin_ids=frozenset({ADMIN}), allow_all=False, bot_name="Sathi")
    store = Store(":memory:")
    access = Access(settings, store)
    chain = Chain([FakeProvider("gemini", list(replies) * 5)], store=store)
    http = None  # not used: search/weather are never called in these flows
    assistant = Assistant(settings, store, chain, access, SearchClient(None), Weather(None))
    session = RecorderSession()
    bot = Bot(token="123456:TEST-TOKEN-NOT-REAL", session=session)
    dp = Dispatcher()
    dp.message.outer_middleware(tg.AccessGate())
    dp.include_router(tg.router)
    kwargs = dict(assistant=assistant, access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    return dp, bot, session, store, access


@pytest.mark.asyncio
async def test_stranger_gets_private_line_once_and_no_model_call():
    dp, bot, session, store, access = setup()
    kw = dict(assistant=None, access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(OTHER, "hi", 1), **kw)
    await dp.feed_update(bot, make_update(OTHER, "hello again", 2), **kw)
    texts = [p.get("text") for name, p in session.sent if name == "SendMessage"]
    assert texts == [persona.PRIVATE_TEXT]  # second message inside 10 minutes is silent


@pytest.mark.asyncio
async def test_admin_text_gets_model_reply():
    dp, bot, session, store, access = setup(["Namaste! Kaise ho?"])
    kw = dict(assistant=setup_assistant(store, access), access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(ADMIN, "namaste"), **kw)
    texts = [p.get("text") for name, p in session.sent if name == "SendMessage"]
    assert texts == ["Namaste! Kaise ho?"]
    assert any(name == "SendChatAction" for name, _ in session.sent)  # typing indicator was sent


def setup_assistant(store, access):
    settings = access.settings
    chain = Chain([FakeProvider("gemini", ["Namaste! Kaise ho?"] * 5)], store=store)
    return Assistant(settings, store, chain, access, SearchClient(None), Weather(None))


@pytest.mark.asyncio
async def test_admin_allow_then_user_can_chat_and_deny_closes_again():
    dp, bot, session, store, access = setup()
    asst = setup_assistant(store, access)
    kw = dict(assistant=asst, access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(ADMIN, f"/allow {OTHER}", 1), **kw)
    assert access.is_allowed(OTHER)
    session.sent.clear()
    await dp.feed_update(bot, make_update(OTHER, "hi", 2), **kw)
    assert any(p.get("text") == "Namaste! Kaise ho?" for n, p in session.sent if n == "SendMessage")
    session.sent.clear()
    await dp.feed_update(bot, make_update(ADMIN, f"/deny {OTHER}", 3), **kw)
    assert not access.is_allowed(OTHER)


@pytest.mark.asyncio
async def test_non_admin_cannot_run_allow():
    dp, bot, session, store, access = setup()
    kw = dict(assistant=setup_assistant(store, access), access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(OTHER, f"/allow {OTHER}", 1), **kw)
    assert not access.is_allowed(OTHER)


@pytest.mark.asyncio
async def test_group_message_without_mention_is_ignored():
    dp, bot, session, store, access = setup()
    kw = dict(assistant=setup_assistant(store, access), access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(ADMIN, "random chat", 1, chat_type="group"), **kw)
    assert not [n for n, _ in session.sent if n == "SendMessage"]


@pytest.mark.asyncio
async def test_start_and_reset_commands_answer_for_admin():
    dp, bot, session, store, access = setup()
    kw = dict(assistant=setup_assistant(store, access), access=access, bot_username="sathi_test_bot", bot_id=BOT_ID)
    await dp.feed_update(bot, make_update(ADMIN, "/start", 1), **kw)
    await dp.feed_update(bot, make_update(ADMIN, "/reset", 2), **kw)
    texts = [p.get("text") for n, p in session.sent if n == "SendMessage"]
    assert texts[0].startswith("Hi, I'm Sathi")
    assert "clear" in texts[1]
