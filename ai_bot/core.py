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
from . import docs as docs_mod
from . import sandbox as sandbox_mod
from .githubread import GithubError
from .images import ImageUnavailable
from .textutil import clean_reply
from .web import FetchFailed, UnsafeURL, fetch_text
from .tools import SearchClient, SearchUnavailable, Weather, WeatherUnavailable

log = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    provider: str | None = None
    kind: str = "ok"  # ok | limit | down | off | fail
    image: bytes | None = None
    file: tuple[str, bytes] | None = None


def parse_tool(reply: str) -> tuple[str, str] | None:
    s = (reply or "").strip()
    if len(s) > 700:
        return None
    up = s.upper()
    for tag, kind in (("SEARCH:", "search"), ("WEATHER:", "weather"), ("LINK:", "link"), ("GITHUB:", "github"), ("IMAGE:", "image"), ("DOC:", "doc")):
        if up.startswith(tag):
            arg = s[len(tag):].strip().splitlines()[0].strip() if s[len(tag):].strip() else ""
            return (kind, arg) if arg else None
    return None


class Assistant:
    def __init__(self, settings: Settings, store: Store, chain: Chain, access: Access,
                 search: SearchClient, weather: Weather, stt=None, images=None, github=None, fetcher=None) -> None:
        self.settings = settings
        self.store = store
        self.chain = chain
        self.access = access
        self.search = search
        self.weather = weather
        self.images = images      # object with async generate(prompt) -> bytes
        self.github = github      # object with async read(spec) -> str
        self.fetcher = fetcher or fetch_text
        self._heavy: dict[tuple[str, int], int] = {}
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

    async def _run_tool(self, kind: str, arg: str, user_id: int = 0) -> str:
        self._cur_user = user_id or getattr(self, "_cur_user", 0)
        if kind == "search":
            try:
                hits = await self.search.search(arg)
            except SearchUnavailable:
                return f"[search: {arg}] search service unavailable"
            if not hits:
                return f"[search: {arg}] no results"
            lines = [f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits[:5]]
            return f"[search results for '{arg}']\n" + "\n".join(lines)
        if kind == "link":
            return await self.read_link(self._cur_user, arg)
        if kind == "github":
            return await self.read_github(self._cur_user, arg)
        try:
            res = await self.weather.current(arg)
        except WeatherUnavailable:
            return f"[weather: {arg}] weather service unavailable"
        return f"[weather]\n{res}" if res else f"[weather: {arg}] place not found"

    # ---- heavy features (images, files, links, GitHub) -------------------
    def _heavy_ok(self, user_id: int) -> bool:
        if self.access.is_admin(user_id):
            return True
        return self._heavy.get((self.today(), user_id), 0) < self.settings.heavy_daily_limit

    def _heavy_count(self, user_id: int) -> None:
        k = (self.today(), user_id)
        self._heavy = {kk: v for kk, v in self._heavy.items() if kk[0] == k[0]}
        self._heavy[k] = self._heavy.get(k, 0) + 1

    async def make_image(self, user_id: int, prompt: str) -> Reply:
        if not prompt.strip():
            return Reply("Tell me what to draw, e.g. /image a red fort at sunset", kind="fail")
        if not self._heavy_ok(user_id):
            return Reply(persona.HEAVY_LIMIT_TEXT, kind="limit")
        if self.images is None:
            return Reply(persona.IMAGE_DOWN_TEXT, kind="off")
        try:
            data = await self.images.generate(prompt)
        except ImageUnavailable:
            return Reply(persona.IMAGE_DOWN_TEXT, kind="off")
        self._heavy_count(user_id)
        return Reply("", kind="ok", image=data)

    async def read_link(self, user_id: int, url: str) -> str:
        if not self._heavy_ok(user_id):
            return persona.HEAVY_LIMIT_TEXT
        try:
            text = await self.fetcher(url)
        except UnsafeURL as e:
            return f"[link: {url}] blocked: {e}"
        except FetchFailed as e:
            return f"[link: {url}] could not be read: {e}"
        self._heavy_count(user_id)
        return f"[page content of {url}]\n{text}"

    async def read_github(self, user_id: int, spec: str) -> str:
        if not self._heavy_ok(user_id):
            return persona.HEAVY_LIMIT_TEXT
        if self.github is None:
            return "[github] not available"
        try:
            text = await self.github.read(spec)
        except GithubError as e:
            return f"[github: {spec}] {e}"
        self._heavy_count(user_id)
        return f"[github data]\n{text[:7000]}"

    async def make_document(self, chat_id: int, user_id: int, fmt: str, title: str, brief: str) -> Reply:
        if not self._heavy_ok(user_id):
            return Reply(persona.HEAVY_LIMIT_TEXT, kind="limit")
        fmt = (fmt or "").strip().lower().lstrip(".")
        if fmt not in docs_mod.FORMATS:
            fmt = docs_mod.guess_format(f"{fmt} {brief}")
        sys_p = (self._system(chat_id) + "\n\nWrite the full content of the requested document. Output ONLY the content, no intro or "
                 "closing words, no code fences. Use '# ' for headings and '- ' for bullets. For spreadsheets and csv write rows "
                 "with columns separated by ' | ', first row = column names.")
        try:
            body, _ = await self.chain.generate([{"role": "user", "content": f"Title: {title}\nRequest: {brief}"}], sys_p)
        except AllProvidersDown:
            return Reply(persona.DOWN_TEXT, kind="down")
        body = clean_reply(body)
        try:
            name, data = docs_mod.make_doc(fmt, title or "document", body)
        except docs_mod.DocError as e:
            return Reply(str(e), kind="fail")
        self._heavy_count(user_id)
        return Reply(f"Here is your {fmt} file ✅", kind="ok", file=(name, data))

    async def edit_document(self, chat_id: int, user_id: int, filename: str, data: bytes, instruction: str) -> Reply:
        if not self._heavy_ok(user_id):
            return Reply(persona.HEAVY_LIMIT_TEXT, kind="limit")
        try:
            fmt, text = docs_mod.extract_text(filename, data)
        except docs_mod.DocError as e:
            return Reply(str(e), kind="fail")
        if not instruction.strip():
            return Reply("Send the file again with a caption saying what to change, e.g. 'fix the grammar' or 'translate to Hindi'.", kind="fail")
        sys_p = (self._system(chat_id) + "\n\nYou edit documents. Apply the user's instruction to the document text and output ONLY the complete new "
                 "document text, no comments, no code fences. Keep '# ' headings, '- ' bullets and ' | ' table columns where they exist.")
        try:
            new, _ = await self.chain.generate(
                [{"role": "user", "content": f"Instruction: {instruction}\n\nDocument ({filename}):\n{text}"}], sys_p)
        except AllProvidersDown:
            return Reply(persona.DOWN_TEXT, kind="down")
        out_fmt = fmt if fmt in docs_mod.FORMATS else "txt"
        try:
            base_name = filename.rsplit(".", 1)[0]
            name, out = docs_mod.make_doc(out_fmt, base_name + " edited", clean_reply(new))
        except docs_mod.DocError as e:
            return Reply(str(e), kind="fail")
        self._heavy_count(user_id)
        note = "Edited ✅ (I rebuild the file from its text, so fancy layout, colours and images are not kept.)"
        return Reply(note, kind="ok", file=(name, out))

    async def run_code(self, user_id: int, code: str) -> str:
        if not self.access.is_admin(user_id):
            return ""
        if not self.settings.enable_admin_sandbox:
            return persona.SANDBOX_OFF_TEXT
        code = code.strip().strip("`")
        if code.lower().startswith("python"):
            code = code[6:].lstrip()
        if not code:
            return "Use: /run print(2+2)"
        return await sandbox_mod.run_python(code)

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
    async def answer_text(self, chat_id: int, user_id: int, text: str, forced_tool: tuple[str, str] | None = None) -> Reply:
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        self.store.add_message(chat_id, "user", text)
        history = [{"role": m["role"], "content": m["content"]} for m in self.store.recent(chat_id, self._window())]
        system = self._system(chat_id)
        try:
            if forced_tool:
                reply, provider = "", None
                tool = forced_tool
            else:
                reply, provider = await self.chain.generate(history, system + "\n\n" + persona.TOOL_RULES)
                tool = parse_tool(reply)
            if tool and tool[0] == "image":
                res = await self.make_image(user_id, tool[1])
                self.store.add_message(chat_id, "assistant", f"[picture: {tool[1][:80]}]" if res.image else res.text)
                if res.image:
                    self._count_user(user_id)
                return res
            if tool and tool[0] == "doc":
                parts = [x.strip() for x in tool[1].split("|", 2)]
                fmt, title, brief = (parts + ["", "", ""])[:3] if len(parts) == 3 else ("", parts[0], parts[-1])
                res = await self.make_document(chat_id, user_id, fmt, title, brief or title)
                self.store.add_message(chat_id, "assistant", f"[file: {res.file[0]}]" if res.file else res.text)
                if res.file:
                    self._count_user(user_id)
                return res
            if tool:
                kind, arg = tool
                result = await self._run_tool(kind, arg, user_id)
                follow = history + [{"role": "user", "content": f"{result}\n\nNow answer my question using this, in the language I wrote in."}]
                reply, provider = await self.chain.generate(follow, system + "\n\n" + persona.RESULT_RULES)
                if parse_tool(reply):
                    reply = "I couldn't get reliable info on that right now. Please try again in a little while."
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
