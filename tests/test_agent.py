import asyncio
from types import SimpleNamespace as NS

from ai_bot.config import Settings
from ai_bot.core import Assistant
from ai_bot.access import Access
from ai_bot.store import Store
from tests.test_e2b import mk


class ScriptChain:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.last_provider = "fake"

    async def generate(self, msgs, system, **kw):
        self.calls.append(msgs[-1]["content"])
        return self.replies.pop(0), "fake"

    def status(self):
        return []


def build(replies, tmp_path, key=True):
    s = Settings(admin_ids=frozenset({1}), db_path=str(tmp_path / "a.db"), e2b_api_key="k" * 12 if key else "")
    st = Store(s.db_path)
    box, created = mk()
    a = Assistant(s, st, ScriptChain(replies), Access(s, st), None, None, box=box if key else None)
    return a, created


def test_agent_runs_code_then_answers(tmp_path):
    a, created = build(["RUNPY:\nprint('hi')", "The program printed hi."], tmp_path)
    r = asyncio.run(a.answer_text(5, 1, "run a hello program"))
    assert r.text == "The program printed hi." and created


def test_agent_fixes_error_and_is_admin_only(tmp_path):
    a, _ = build(["RUNPY:\nboom", "RUNPY:\nprint('hi')", "fixed"], tmp_path)
    assert asyncio.run(a.answer_text(5, 1, "do it")).text == "fixed"
    assert "ValueError" in a.chain.calls[1]
    a2, c2 = build(["RUNPY:\nprint(1)"], tmp_path)
    r = asyncio.run(a2.answer_text(5, 2, "hi"))  # user 2 is not an admin: the tag is not executed
    assert not c2


def test_sandbox_commands_need_admin_and_key(tmp_path):
    a, _ = build([], tmp_path)
    assert asyncio.run(a.run_code(2, "print(1)", 5)) == ""
    assert asyncio.run(a.run_code(1, "print('hi')", 5)).strip() == "hi"
    b, _ = build([], tmp_path / "x" if False else tmp_path, key=False)
    assert "switched off" in asyncio.run(b.run_code(1, "print(1)", 5))
    assert "E2B" in asyncio.run(b.run_shell(1, 5, "ls"))
