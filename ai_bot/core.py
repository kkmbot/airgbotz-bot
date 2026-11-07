"""The assistant's brain, free of Telegram: memory, tool routing, caps, commands. Tested with fake providers."""
from __future__ import annotations

import logging
import json
import re
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
from . import extras
from .e2b_box import BoxUnavailable
from .images import ImageUnavailable, add_caption
from . import astro as astro_mod
from . import photoedit as photoedit_mod
from .textutil import clean_reply
import httpx

from .web import FetchFailed, UnsafeURL, fetch_text, fetch_via_browser
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
    for tag, kind in (("SEARCH:", "search"), ("WEATHER:", "weather"), ("LINK:", "link"), ("GITHUB:", "github"), ("IMAGE:", "image"), ("DOC:", "doc"), ("ASTRO:", "astro"), ("MATCH:", "match")):
        if up.startswith(tag):
            arg = s[len(tag):].strip().splitlines()[0].strip() if s[len(tag):].strip() else ""
            return (kind, arg) if arg else None
    return None


_URL = re.compile(r"https?://[^\s<>\")]+", re.I)


def auto_tool(text: str) -> tuple[str, str] | None:
    """A short message that carries a link is read without any command: GitHub links via the API, others as pages."""
    t = text.strip()
    m = _URL.search(t)
    if not m or len(t) > 500:
        return None
    url = m.group(0).rstrip(".,;:!?")
    if re.match(r"https?://(www\.)?github\.com/[\w.-]+/[\w.-]+", url, re.I):
        return ("github", url)
    return ("link", url)


class Assistant:
    def __init__(self, settings: Settings, store: Store, chain: Chain, access: Access,
                 search: SearchClient, weather: Weather, stt=None, images=None, github=None, fetcher=None, box=None, judge0=None) -> None:
        self.settings = settings
        self.store = store
        self.chain = chain
        self.access = access
        self.search = search
        self.weather = weather
        self.box = box            # E2BBox or None
        self.judge0 = judge0      # free Judge0 fallback or None
        self._sbx_runs: dict[tuple[str, int], int] = {}
        self.images = images      # object with async generate(prompt) -> bytes
        self.github = github      # object with async read(spec) -> str
        self.fetcher = fetcher or fetch_text
        self.http = httpx.AsyncClient(timeout=45)
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
        notes = self.store.notes(chat_id)
        if notes:
            parts.append("Things this user asked you to remember (treat as true):\n" + "\n".join("- " + n["text"] for n in notes[-40:]))
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
        if kind == "match":
            try:
                return "[kundli milan, calculated]\n" + await astro_mod.match(self.http, arg)
            except astro_mod.AstroError as e:
                return f"[kundli milan] could not be made: {e}. Ask the user for the missing detail."
            except Exception:  # noqa: BLE001
                return "[kundli milan] calculation failed. Say so honestly and ask them to retry."
        if kind == "astro":
            try:
                return "[birth chart, calculated with real planetary positions]\n" + await astro_mod.chart(self.http, arg)
            except astro_mod.AstroError as e:
                return f"[birth chart] could not be made: {e}. Ask the user for the missing detail."
            except Exception:  # noqa: BLE001
                return "[birth chart] calculation failed. Say so honestly and ask them to retry."
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
            caption = ""
            if "||" in prompt:
                prompt, _, rest = prompt.partition("||")
                caption = re.sub(r"^\s*TEXT:\s*", "", rest, flags=re.I).strip()
                prompt = prompt.strip() + ", no text, no letters, no words, no watermark"
            data = await self.images.generate(prompt)
            if caption:
                data = add_caption(data, caption)
        except ImageUnavailable:
            return Reply(persona.IMAGE_DOWN_TEXT, kind="off")
        self._heavy_count(user_id)
        return Reply("", kind="ok", image=data)

    async def read_link(self, user_id: int, url: str) -> str:
        if not self._heavy_ok(user_id):
            return persona.HEAVY_LIMIT_TEXT
        try:
            try:
                text = await self.fetcher(url)
                if len(text.split("\n\n", 1)[-1].strip()) < 200 and self.settings.browserless_api_key:
                    raise FetchFailed("almost no text (probably a JavaScript page)")
            except FetchFailed as first:
                if not self.settings.browserless_api_key or "screenshot" in str(first):
                    raise
                text = await fetch_via_browser(url, self.settings.browserless_api_key, self.http)
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

    async def _edit_pdf_inplace(self, chat_id: int, filename: str, data: bytes, instruction: str):
        """Returns (file name, bytes) with the original layout kept, or None to fall back to the rebuild path."""
        from . import pdfedit
        try:
            blocks = pdfedit.list_blocks(data)
        except pdfedit.PdfEditError:
            return None
        listing = json.dumps([{"id": b["id"], "text": b["text"]} for b in blocks], ensure_ascii=False)[:60000]
        sys_p = (self._system(chat_id) + "\n\nYou edit a PDF block by block. You get a JSON list of text blocks {id,text}. Apply the user's instruction "
                 "and answer with ONLY a JSON object mapping the id (as a string) of each block that must CHANGE to its complete new text. "
                 "Leave unchanged blocks out. Keep each new text about as long as the old one when possible. No code fences, no commentary.")
        try:
            raw, _ = await self.chain.generate([{"role": "user", "content": f"Instruction: {instruction}\n\nBlocks:\n{listing}"}], sys_p)
        except AllProvidersDown:
            raise
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            edits = {int(k): str(v) for k, v in obj.items() if str(k).isdigit() and 0 <= int(k) < len(blocks)}
        except (ValueError, TypeError):
            return None
        if not edits:
            return None
        try:
            out = pdfedit.apply_edits(data, edits)
        except Exception:  # noqa: BLE001 - any layout problem: use the safe rebuild path instead
            return None
        return filename.rsplit(".", 1)[0] + " edited.pdf", out

    _OFFICIAL = re.compile(r"(?i)(salary|payslip|pay[ _-]?slip|bank[ _-]?statement|aadhaar|aadhar|\\bpan\\b|marksheet|mark[ _-]?sheet|degree|certificate|passport|form[ _-]?16|itr|offer[ _-]?letter|experience[ _-]?letter|relieving)")
    _CHANGE = re.compile(r"(?i)(name|naam|month|mahin|date|tarikh|salary|amount|rupee|₹|rs\\b|number|change|badal|replace|karwana|kar do|karo)")

    async def _ocr_pdf(self, data: bytes, max_pages: int = 5) -> str:
        """Scanned PDF: draw each page as a picture and let the vision model read it (Hindi and English)."""
        import pymupdf as fitz  # PyMuPDF
        out = []
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception:  # noqa: BLE001
            return ""
        for n, page in enumerate(doc):
            if n >= max_pages:
                out.append(f"[only the first {max_pages} pages were read]")
                break
            try:
                jpg = page.get_pixmap(dpi=130).tobytes("jpeg")
                txt, _ = await self.chain.generate(
                    [{"role": "user", "content": photoedit_mod.OCR_PROMPT}], "You are an accurate OCR engine.",
                    image=jpg, mime="image/jpeg", vision=True)
            except AllProvidersDown:
                break
            except Exception:  # noqa: BLE001
                continue
            out.append(clean_reply(txt))
        return "\n\n".join(x for x in out if x)

    async def edit_document(self, chat_id: int, user_id: int, filename: str, data: bytes, instruction: str) -> Reply:
        if instruction.strip() and self._OFFICIAL.search(filename) and self._CHANGE.search(instruction):
            return Reply("I can't change names, dates or amounts on official papers like salary slips, bank statements, IDs or certificates, "
                         "because that makes a fake document. I can fix spelling or formatting, translate it, or build a clearly marked sample/template "
                         "for you to fill in with your own real details.", kind="fail")
        if not self._heavy_ok(user_id):
            return Reply(persona.HEAVY_LIMIT_TEXT, kind="limit")
        try:
            fmt, text = docs_mod.extract_text(filename, data)
        except docs_mod.DocError as e:
            return Reply(str(e), kind="fail")
        scanned = False
        if fmt == "pdf" and len(text.strip()) < 20:
            if not self._heavy_ok(user_id):
                return Reply(persona.HEAVY_LIMIT_TEXT, kind="limit")
            text = await self._ocr_pdf(data)
            if len(text.strip()) < 20:
                return Reply("Is PDF me mujhe text nahi mila (scan bahut dhundla ya khaali hai). Saaf photo bhejo to padh dunga 🙏", kind="fail")
            scanned = True
            self._heavy_count(user_id)
        if not instruction.strip():
            try:
                summ, prov = await self.chain.generate(
                    [{"role": "user", "content": f"Summarise this document briefly in the language it is written in, then ask what I want changed:\n\n{text}"}],
                    self._system(chat_id))
            except AllProvidersDown:
                return Reply(persona.DOWN_TEXT, kind="down")
            self._count_user(user_id)
            return Reply(clean_reply(summ) + "\n\n(Send the file again with a caption like 'fix the grammar' or 'translate to Hindi' and I will return the edited file.)", prov, "ok")
        if filename.lower().endswith(".pdf") and instruction.strip() and not scanned:
            try:
                done = await self._edit_pdf_inplace(chat_id, filename, data, instruction)
            except AllProvidersDown:
                return Reply(persona.DOWN_TEXT, kind="down")
            if done:
                self._heavy_count(user_id)
                return Reply("Edited ✅ Your original PDF layout, images and colours are kept; only the changed text was replaced. Check it once, long lines may look slightly smaller.", kind="ok", file=done)
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
            name, out = docs_mod.make_doc(out_fmt, base_name + " edited", clean_reply(new), with_title=False)
        except docs_mod.DocError as e:
            return Reply(str(e), kind="fail")
        self._heavy_count(user_id)
        note = "Edited ✅ (I rebuild the file from its text, so fancy layout, colours and images are not kept.)"
        return Reply(note, kind="ok", file=(name, out))

    # ---- admin sandbox (E2B VM, or the weaker local runner) -------------
    def sandbox_mode(self) -> str:
        s = self.settings
        if self.box is not None and self.box.configured() and s.sandbox_backend in ("auto", "e2b"):
            return "e2b"
        if self.judge0 is not None and s.sandbox_backend in ("auto", "judge0"):
            return "judge0"
        if s.enable_admin_sandbox and s.sandbox_backend in ("auto", "local"):
            return "local"
        return "off"

    def _sbx_ok(self) -> bool:
        k = (self.today(), 0)
        self._sbx_runs = {kk: v for kk, v in self._sbx_runs.items() if kk[0] == k[0]}
        if self._sbx_runs.get(k, 0) >= self.settings.sandbox_daily_limit:
            return False
        self._sbx_runs[k] = self._sbx_runs.get(k, 0) + 1
        return True

    async def run_code(self, user_id: int, code: str, chat_id: int = 0) -> str:
        if not self.access.is_admin(user_id):
            return ""
        mode = self.sandbox_mode()
        if mode == "off":
            return persona.SANDBOX_OFF_TEXT
        code = code.strip().strip("`")
        if code.lower().startswith("python"):
            code = code[6:].lstrip()
        if not code:
            return "Use: /run print(2+2)"
        if not self._sbx_ok():
            return "Today's sandbox limit is over 🙏"
        if mode == "local":
            return await sandbox_mod.run_python(code)
        if mode == "judge0":
            return await self.judge0.run_python(chat_id, code)
        try:
            return await self.box.run_python(chat_id, code)
        except BoxUnavailable as e:
            return str(e)

    async def run_shell(self, user_id: int, chat_id: int, cmd: str) -> str:
        if not self.access.is_admin(user_id):
            return ""
        if self.sandbox_mode() != "e2b":
            return "The shell needs the E2B sandbox (set E2B_API_KEY)."
        if not cmd.strip():
            return "Use: /sh ls -la   or   /sh pip install pandas"
        if not self._sbx_ok():
            return "Today's sandbox limit is over 🙏"
        try:
            return await self.box.run_shell(chat_id, cmd.strip())
        except BoxUnavailable as e:
            return str(e)

    async def sandbox_files(self, user_id: int, chat_id: int, path: str = "") -> str:
        if not self.access.is_admin(user_id) or self.sandbox_mode() != "e2b":
            return "Files need the E2B sandbox (set E2B_API_KEY)."
        try:
            return await self.box.list_files(chat_id, path.strip() or "/home/user")
        except BoxUnavailable as e:
            return str(e)

    async def sandbox_get(self, user_id: int, chat_id: int, path: str) -> Reply:
        if not self.access.is_admin(user_id) or self.sandbox_mode() != "e2b":
            return Reply("Files need the E2B sandbox (set E2B_API_KEY).", kind="off")
        path = path.strip()
        if not path:
            return Reply("Use: /files to list, then /files /home/user/chart.png to get one", kind="fail")
        try:
            data = await self.box.read_file(chat_id, path)
        except BoxUnavailable as e:
            return Reply(str(e), kind="fail")
        return Reply("", kind="ok", file=(path.rsplit("/", 1)[-1] or "file", data))

    async def sandbox_put(self, user_id: int, chat_id: int, name: str, data: bytes) -> str | None:
        """Copy an uploaded file into the sandbox. Returns the path, or None when not applicable."""
        if not self.access.is_admin(user_id) or self.sandbox_mode() != "e2b":
            return None
        try:
            return await self.box.write_file(chat_id, name, data)
        except BoxUnavailable:
            return None

    async def agent_loop(self, chat_id: int, user_id: int, history: list, system: str, reply: str, provider):
        """The model asked to run code: run it, show it the result, repeat (max 5 rounds)."""
        msgs = list(history)
        for _ in range(5):
            m = re.match(r"(?is)\s*RUNPY:\s*\n?(.*)", reply)
            s = re.match(r"(?is)\s*SENDFILE:\s*(\S+)", reply)
            if s:
                res = await self.sandbox_get(user_id, chat_id, s.group(1))
                if res.file:
                    res.text = "Here is the file ✅"
                return res
            if not m:
                return Reply(clean_reply(reply) or "Done.", provider, "ok")
            code = m.group(1).strip().strip("`")
            if code.lower().startswith("python"):
                code = code[6:].lstrip()
            out = await self.run_code(user_id, code, chat_id)
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"[sandbox result]\n{out}\n\nIf the task is done, answer me in my language (reply SENDFILE: <path> to send a file you made). "
                                                 "If there was an error, fix it and reply RUNPY: again."}]
            reply, provider = await self.chain.generate(msgs, system + "\n\n" + persona.TOOL_RULES + persona.AGENT_RULES)
        return Reply(clean_reply(reply) or "I could not finish that in 5 tries.", provider, "ok")

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
    def memory_command(self, chat_id: int, text: str) -> str | None:
        """'remember ...', 'what do you remember', 'forget my notes' in plain words. Notes live only until the host restarts."""
        t = text.strip()
        low = t.lower()
        m = re.match(r"(?is)^(please\s+)?(remember|yaad rakh(?:na|o)?|yaad rakho)\b[\s:,-]*(that\s+)?(.+)$", t)
        if m and len(m.group(4)) >= 3 and len(t) < 500:
            if len(self.store.notes(chat_id)) >= 40:
                return "My notes list is full (40). Say \"forget my notes\" first."
            self.store.add_note(chat_id, m.group(4).strip())
            return "Noted ✅ (I will remember it in this chat)"
        if re.fullmatch(r"(what (do you|u) remember|my notes|show (my )?notes|kya yaad hai|kya kya yaad hai)\??[.!]*", low):
            ns = self.store.notes(chat_id)
            return "\n".join(f"{i}. {n['text']}" for i, n in enumerate(ns, 1)) if ns else "Nothing saved yet. Say \"remember ...\"."
        if re.fullmatch(r"(please )?(forget (my |all )?(notes|everything you remember)|notes (clear|delete) kar( do)?)[.!]*", low):
            self.store.clear_notes(chat_id)
            return "Notes cleared ✅"
        return None

    def natural_command(self, user_id: int, text: str) -> str | None:
        """A few housekeeping wishes said in plain words, so no slash command is needed."""
        t = text.strip().lower()
        if len(t) > 60:
            return None
        if re.fullmatch(r"(please )?(reset|clear|forget)( (this )?(chat|memory|everything))?[.!]*|(yaad|memory|chat) (clear|reset|saaf) kar( do| de)?[.!]*", t):
            return "reset"
        if re.fullmatch(r"(which|what|kaun ?sa?) (ai|model)( (is )?(answering|chal raha hai|use ho raha hai))?\??|model status", t):
            return "model"
        if self.access.is_admin(user_id) and re.fullmatch(r"(today'?s? )?(usage|aaj ka usage)\??", t):
            return "usage"
        return None

    async def answer_text(self, chat_id: int, user_id: int, text: str, forced_tool: tuple[str, str] | None = None) -> Reply:
        if not forced_tool:
            q = extras.qr_request(text)
            if q:
                return Reply("QR code ✅", kind="ok", image=extras.make_qr(q))
            mr = extras.money_request(text)
            if mr:
                return Reply(await extras.money_answer(self.http, mr))
        mem = None if forced_tool else self.memory_command(chat_id, text)
        if mem is not None:
            return Reply(mem)
        nat = None if forced_tool else self.natural_command(user_id, text)
        if nat == "reset":
            return Reply(self.reset(chat_id))
        if nat == "model":
            return Reply(self.model_status())
        if nat == "usage":
            return Reply(self.usage_text())
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        self.store.add_message(chat_id, "user", text)
        history = [{"role": m["role"], "content": m["content"]} for m in self.store.recent(chat_id, self._window())]
        system = self._system(chat_id)
        try:
            auto = None if forced_tool else auto_tool(text)
            if forced_tool or auto:
                reply, provider = "", None
                tool = forced_tool or auto
            else:
                agent = self.access.is_admin(user_id) and self.sandbox_mode() in ("e2b", "judge0")
                reply, provider = await self.chain.generate(
                    history, system + "\n\n" + persona.TOOL_RULES + (persona.AGENT_RULES if agent else ""))
                if agent and re.match(r"(?is)\s*(RUNPY|SENDFILE):", reply):
                    res = await self.agent_loop(chat_id, user_id, history, system, reply, provider)
                    self.store.add_message(chat_id, "assistant", res.text or (f"[file: {res.file[0]}]" if res.file else "done"))
                    self._count_user(user_id)
                    return res
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

    async def edit_photo(self, chat_id: int, user_id: int, image: bytes, instruction: str) -> Reply:
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        try:
            raw, provider = await self.chain.generate([{"role": "user", "content": instruction[:500]}], photoedit_mod.SYSTEM)
        except AllProvidersDown:
            return Reply(persona.PHOTO_DOWN_TEXT, kind="off")
        ops, cannot = photoedit_mod.parse_ops(raw)
        if not ops:
            msg = ("Ye edit main code se nahi kar sakta" + (f" ({cannot})" if cannot else "") +
                   ". Mai ye kar sakta hun: crop, rotate, flip, resize, black & white, sepia, vintage, brightness/contrast/colour, blur, sharpen, border, aur photo pe text.")
            return Reply(msg, provider, "ok")
        try:
            data = photoedit_mod.apply_ops(image, ops)
        except Exception:  # noqa: BLE001
            return Reply("Is photo pe edit nahi ho paya, dusri photo try karo 🙏", kind="fail")
        self.store.add_message(chat_id, "user", f"[photo edit] {instruction[:200]}")
        self.store.add_message(chat_id, "assistant", "[edited photo sent]")
        self._count_user(user_id)
        names = ", ".join(o["op"] for o in ops)
        return Reply(f"Ho gaya ✅ ({names})", provider, "ok", image=data)

    async def answer_photo(self, chat_id: int, user_id: int, image: bytes, mime: str, caption: str) -> Reply:
        if not self._check_cap(user_id):
            return Reply(persona.LIMIT_TEXT, kind="limit")
        question = photoedit_mod.photo_question(caption)
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
