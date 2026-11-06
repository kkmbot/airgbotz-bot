import asyncio
from types import SimpleNamespace as NS

from ai_bot.telegram_app import AdminLog


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, parse_mode=None):
        self.sent.append((chat, text))


class FakeAccess:
    def is_admin(self, uid):
        return uid == 1

    def is_allowed(self, uid):
        return True


def run(user_id, text="hello secret text", boom=False):
    bot = FakeBot()
    mw = AdminLog(bot, [1])
    ev = NS(from_user=NS(id=user_id, full_name="Ravi"), text=text, photo=None, voice=None, audio=None, document=None)

    async def handler(e, d):
        if boom:
            raise ValueError("x")
        return "ok"

    async def go():
        try:
            return await mw(handler, ev, {"access": FakeAccess()})
        except ValueError:
            return "raised"
    return asyncio.run(go()), bot.sent


def test_other_user_is_logged_without_text():
    res, sent = run(5)
    assert res == "ok" and len(sent) == 1 and "Ravi" in sent[0][1] and "secret" not in sent[0][1]


def test_admin_not_logged_and_errors_reported():
    assert run(1)[1] == []
    res, sent = run(1, boom=True)
    assert res == "raised" and "Error" in sent[0][1]
