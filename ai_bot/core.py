"""The assistant's brain, free of Telegram: memory, tool routing, caps, commands. Tested with fake providers."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import persona
from .access import Access
from .chain import AllProvidersDown, Chain
from .config import Settings
from .store import Store
from .textutil import clean_reply
from .tools import SearchClient, SearchUnavailable, Weather, WeatherUnavailable

log = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    provider: str | None = None
    kind: str = "ok"  # ok | limit | down | off | fail


def parse_tool(reply: str) -> tuple[str, str] | None:
    s = (reply or "").strip()
    if len(s) > 300:
        return None
    up = s.upper()
    for tag, kind in (("SEARCH:", "search"), ("WEATHER:", "weather")):
        if up.startswith(tag):
            arg = s[len(tag):].strip().splitlines()[0].strip() if s[len(tag):].strip() else ""
            return (kind, arg) if arg else None
    return None


class Assistant:
    def __init__(self, settings: Settings, store: Store, chain: Chain, access: Access,
                 search: SearchClient, weather: Weather, stt=None) -> None:
        self.settings = settings
        self.store = store
        self.chain = chain
        self.access = access
        self.search = search
        self.weather = weather
        self.stt = stt  # object with transcribe(audio, filename, mime, model) and configured()
        self.tz = timezone(timedelta(minutes=settings.tz_offset_minutes))

    # ---- helpers ---------------------------------------------------------
    def today(self) -> str:
        return datetime.now(self.tz).strftime("%Y-%m-%d")

    def _window(self) -> int:
        return self.settings.memory_turns * 2

    def _system(self, chat_id: int, extra: str = "") -> str:
        now = datetime.now(self.tz).strftime("%A %d %B %Y, %H:%M")
        parts = [persona.PERSONA.format(name=self.settings.bot_name), f"Current date and time (IST): {now}."]
        summary = self.store.get_summary(chat_id)
        if summary:
            parts.append(f"What you already know about this user from earlier chat: {summary}")
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    def _check_cap(self, user_id: int) -> bool:
        if self.access.is_admin(user_id):
            return True
        return self.store.get_user_day(self.today(), user_id) < self.settings.daily_user_limit

    def _count_user(self, user_id: int) -> None:
        self.store.bump_user_day(self.today(), user_id)

    async def _run_tool(self, kind: str, arg: str) -> str:
        if kind == "search":
            try:
                hits = await self.search.search(arg)
            except SearchUnavailable:
                return f"[search: {arg}] search service unavailable"
            if not hits:
                return f"[search: {arg}] no results"
            lines = [f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits[:5]]
            return f"[search results for '{arg}']\n" + "\n".join(lines)
        try:
            res = await self.weather.current(arg)
        except WeatherUnavailable:
            return f"[weather: {arg}] weather service unavailable"
        return f"[weather]\n{res}" if res else f"[weather: {arg}] place not found"

    async def _maybe_summarize(self, chat_id: int) -> None:
        window = self._window()
        count = self.store.count_messages(chat_id)
        if count <= window + self.settings.summary_every:
            return
        old = self.store.oldest(chat_id, count - window)
        if not old:
            return
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in old)
        prev = self.store.get_summary(chat_id) or "(none)"
        prompt = [{"role": "user", "content": (
            f"Previous summary:\n{prev}\n\nNew conversation to fold in:\n{transcript}\n\n"
            "Write an updated summary in at most 120 words: the user's name if given, language style, "
            "preferences, and ongoing topics. Plain text, no preamble.")}]
        try:
            summary, _ = await self.chain.generate(prompt, "You write short, factual chat memory notes.")
        except AllProvidersDown:
            return  # try again on a later turn
        self.store.set_summary(chat_id, summary[:2000])
        self.store.delete_messages_upto(chat_id, old[-1]["id"])

    # ---- public API ------------------------------------------------------
    async def answer_text(self, chat_id: int, user_id: int, text: str) -> Reply:
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        self.store.add_message(chat_id, "user", text)
        history = [{"role": m["role"], "content": m["content"]} for m in self.store.recent(chat_id, self._window())]
        system = self._system(chat_id)
        try:
            reply, provider = await self.chain.generate(history, system + "\n\n" + persona.TOOL_RULES)
            tool = parse_tool(reply)
            if tool:
                kind, arg = tool
                result = await self._run_tool(kind, arg)
                follow = history + [{"role": "user", "content": f"{result}\n\nAb is basis par mera sawal jawab do."}]
                reply, provider = await self.chain.generate(follow, system + "\n\n" + persona.RESULT_RULES)
                if parse_tool(reply):
                    reply = "Abhi is baare me pakki info nahi mil paayi. Thodi der baad phir poochho."
        except AllProvidersDown:
            return Reply(persona.DOWN_TEXT, kind="down")
        final = clean_reply(reply) or "Hmm, is baar jawab khaali aaya. Ek baar phir poochho?"
        self.store.add_message(chat_id, "assistant", final)
        self._count_user(user_id)
        await self._maybe_summarize(chat_id)
        return Reply(final, provider, "ok")

    async def answer_photo(self, chat_id: int, user_id: int, image: bytes, mime: str, caption: str) -> Reply:
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        question = caption.strip() or "Ye photo kya hai? Short me batao."
        self.store.add_message(chat_id, "user", f"[photo] {question}")
        try:
            text, provider = await self.chain.generate(
                [{"role": "user", "content": question}], self._system(chat_id),
                image=image, mime=mime, vision=True)
        except AllProvidersDown:
            return Reply(persona.PHOTO_DOWN_TEXT, kind="off")
        final = clean_reply(text)
        self.store.add_message(chat_id, "assistant", final)
        self._count_user(user_id)
        return Reply(final, provider, "ok")

    async def answer_voice(self, chat_id: int, user_id: int, audio: bytes, filename: str, mime: str) -> Reply:
        if self.stt is None or not self.stt.configured():
            return Reply(persona.VOICE_OFF_TEXT, kind="off")
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        try:
            transcript = await self.stt.transcribe(audio, filename, mime)
        except Exception:  # noqa: BLE001 - transcription is best-effort
            log.warning("voice transcription failed")
            return Reply(persona.VOICE_FAIL_TEXT, kind="fail")
        if not transcript:
            return Reply(persona.VOICE_FAIL_TEXT, kind="fail")
        res = await self.answer_text(chat_id, user_id, transcript)
        if res.kind == "ok":
            res.text = f"🎙️ \"{transcript}\"\n\n{res.text}"
        return res

    def reset(self, chat_id: int) -> str:
        self.store.reset_chat(chat_id)
        return "Theek hai, is chat ki purani yaad clear kar di ✅"

    def model_status(self) -> str:
        last = self.chain.last_provider or "abhi tak koi nahi"
        lines = [f"Last jawab: {last}", "Order:"]
        for name, state in self.chain.status():
            lines.append(f"- {name}: {state}")
        return "\n".join(lines)

    def usage_text(self) -> str:
        rows = self.store.usage_for_day(self.today())
        if not rows:
            return "Aaj abhi koi call nahi hui."
        by: dict[str, dict[str, int]] = {}
        for r in rows:
            by.setdefault(r["provider"], {})[r["kind"]] = r["count"]
        lines = [f"Aaj ({self.today()}):"]
        for prov, kinds in by.items():
            lines.append(f"- {prov}: ok {kinds.get('ok', 0)}, error {kinds.get('err', 0)}")
        return "\n".join(lines)
