"""aiogram handlers. Thin: all decisions live in core.Assistant and access.Access."""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from contextlib import asynccontextmanager

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

from . import extras, persona
from .core import Assistant
from .access import Access
from .textutil import split_text

log = logging.getLogger(__name__)
router = Router(name="main")

NEW_CHAT_LABEL = "🆕 New chat"
NEW_CHAT_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=NEW_CHAT_LABEL)]], resize_keyboard=True, is_persistent=True,
)


class AccessGate(BaseMiddleware):
    """Non-allowed users get one polite line per 10 minutes in private chat, then silence."""

    def __init__(self) -> None:
        self._last_denied: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        access: Access = data["access"]
        if user is None or access.is_allowed(user.id):
            return await handler(event, data)
        chat = getattr(event, "chat", None)
        now = time.monotonic()
        if chat is not None and chat.type == "private" and now - self._last_denied.get(user.id, -1e9) > 600:
            self._last_denied[user.id] = now
            try:
                await event.answer(persona.PRIVATE_TEXT)
            except Exception:  # noqa: BLE001
                log.warning("could not send the private-bot line")
        return None


class AdminLog(BaseMiddleware):
    """Tell the admins what happened: one short line per message from someone who is not an admin
    (kind only, never the message text), plus a line when handling a message fails."""

    def __init__(self, bot, admin_ids, log_chat_id: int = 0) -> None:
        self.bot = bot
        self.targets = tuple(admin_ids) + ((log_chat_id,) if log_chat_id else ())

    @staticmethod
    def kind(event) -> str:
        if getattr(event, "photo", None):
            return "photo"
        if getattr(event, "voice", None) or getattr(event, "audio", None):
            return "voice/audio"
        if getattr(event, "document", None):
            return "file"
        t = (getattr(event, "text", None) or "").strip()
        if t.startswith("/"):
            return "command " + t.split()[0].split("@")[0][:20]
        return "text message"

    async def notify(self, text: str) -> None:
        for aid in self.targets:
            try:
                await self.bot.send_message(aid, text, parse_mode=None)
            except Exception:  # noqa: BLE001 - an alert must never break the bot
                log.warning("could not alert an admin")

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        access: Access = data["access"]
        is_admin = bool(user and access.is_admin(user.id))
        if user and not is_admin and access.is_allowed(user.id):
            who = f"{user.full_name or 'user'} (id {user.id})"
            await self.notify(f"👤 {who}: {self.kind(event)}")
        try:
            return await handler(event, data)
        except Exception as e:  # noqa: BLE001
            who = f"id {user.id}" if user else "unknown"
            await self.notify(f"⚠️ Error while handling a message from {who}: {type(e).__name__}")
            raise


@asynccontextmanager
async def typing(bot, chat_id: int):
    stop = asyncio.Event()

    async def pulse() -> None:
        # first beat right away, then every 4 s until the reply is ready (Telegram shows it ~5 s)
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:  # noqa: BLE001 - a missed beat is harmless
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=4)
                return
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(pulse())
    try:
        yield
    finally:
        stop.set()
        await task


async def send_reply(message: Message, res) -> None:
    """Send a core.Reply: picture, file and/or text."""
    try:
        if getattr(res, "image", None):
            await message.answer_photo(BufferedInputFile(res.image, "picture.jpg"))
        if getattr(res, "file", None):
            name, data = res.file
            await message.answer_document(BufferedInputFile(data, name), caption=(res.text or None))
            return
    except TelegramBadRequest:
        await message.answer("I made it but Telegram refused the upload 🙏", parse_mode=None)
        return
    if res.text:
        await send_long(message, res.text)


async def send_long(message: Message, text: str) -> None:
    chunks = split_text(text) or ["(khaali jawab, dobara poochho)"]
    for idx, chunk in enumerate(chunks):
        kb = NEW_CHAT_KB if (idx == len(chunks) - 1 and message.chat.type == "private") else None
        for attempt in range(2):
            try:
                await message.answer(chunk, parse_mode=None, reply_markup=kb)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramBadRequest:
                # never let a formatting problem drop the reply: send the bare characters once
                bare = re.sub(r"[^\x09\x0a\x0d\x20-\uFFFF]", "", chunk)
                try:
                    await message.answer(bare, parse_mode=None)
                except Exception:  # noqa: BLE001
                    log.warning("could not deliver one chunk")
                break


def _addressed(message: Message, bot_username: str, bot_id: int) -> bool:
    """Private chats: always. Groups: only when the bot is @mentioned or replied to."""
    if message.chat.type == "private":
        return True
    text = message.text or message.caption or ""
    if bot_username and f"@{bot_username}".lower() in text.lower():
        return True
    rt = message.reply_to_message
    return bool(rt and rt.from_user and rt.from_user.id == bot_id)


def _strip_mention(text: str, bot_username: str) -> str:
    if not bot_username:
        return text.strip()
    return re.sub(re.escape(f"@{bot_username}"), "", text, flags=re.IGNORECASE).strip()


@router.message(CommandStart())
async def cmd_start(message: Message, assistant: Assistant) -> None:
    await message.answer(persona.START_TEXT.format(name=assistant.settings.bot_name), parse_mode=None, reply_markup=NEW_CHAT_KB if message.chat.type == "private" else None)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(persona.HELP_TEXT, parse_mode=None)


@router.message(Command("reset"))
async def cmd_reset(message: Message, assistant: Assistant) -> None:
    _PENDING_DOCS.pop(message.chat.id, None)
    await message.answer(assistant.reset(message.chat.id), parse_mode=None, reply_markup=NEW_CHAT_KB if message.chat.type == "private" else None)


@router.message(Command("model"))
async def cmd_model(message: Message, assistant: Assistant) -> None:
    await message.answer(assistant.model_status(), parse_mode=None)


@router.message(Command("usage"))
async def cmd_usage(message: Message, assistant: Assistant) -> None:
    await message.answer(assistant.usage_text(), parse_mode=None)


def _parse_id(command: CommandObject) -> int | None:
    arg = (command.args or "").strip()
    return int(arg) if arg.lstrip("-").isdigit() else None


@router.message(Command("allow"))
async def cmd_allow(message: Message, command: CommandObject, access: Access) -> None:
    if not access.is_admin(message.from_user.id):
        return
    uid = _parse_id(command)
    if uid is None:
        await message.answer("Use: /allow 123456789", parse_mode=None)
        return
    access.allow(uid)
    await message.answer(f"Allowed {uid} ✅", parse_mode=None)


@router.message(Command("deny"))
async def cmd_deny(message: Message, command: CommandObject, access: Access) -> None:
    if not access.is_admin(message.from_user.id):
        return
    uid = _parse_id(command)
    if uid is None:
        await message.answer("Use: /deny 123456789", parse_mode=None)
        return
    access.deny(uid)
    await message.answer(f"Denied {uid} ✅ (admins are always allowed)", parse_mode=None)


@router.message(Command("users"))
async def cmd_users(message: Message, access: Access, assistant: Assistant) -> None:
    if not access.is_admin(message.from_user.id):
        return
    ids = access.store.allowed_ids()
    text = "Allowed users: " + (", ".join(str(i) for i in ids) if ids else "none (only admins)")
    text += f"\nALLOW_ALL: {'on' if access.settings.allow_all else 'off'}"
    await message.answer(text, parse_mode=None)


@router.message(Command("image"))
async def cmd_image(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    async with typing(bot, message.chat.id):
        res = await assistant.make_image(message.from_user.id, command.args or "")
    await send_reply(message, res)


@router.message(Command("doc"))
async def cmd_doc(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    from .docs import FORMATS, guess_format
    args = (command.args or "").strip()
    if not args:
        await message.answer("Use: /doc pdf a one-page study plan for maths", parse_mode=None)
        return
    first, _, rest = args.partition(" ")
    if first.lower().lstrip(".") in FORMATS:
        fmt, brief = first.lower().lstrip("."), rest.strip()
    else:
        fmt, brief = guess_format(args), args
    title = " ".join(brief.split()[:6]) or "document"
    async with typing(bot, message.chat.id):
        res = await assistant.make_document(message.chat.id, message.from_user.id, fmt, title, brief or title)
    await send_reply(message, res)


@router.message(Command("read"))
async def cmd_read(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    url = (command.args or "").strip().split()[0] if (command.args or "").strip() else ""
    if not url:
        await message.answer("Use: /read https://example.com/article", parse_mode=None)
        return
    async with typing(bot, message.chat.id):
        res = await assistant.answer_text(message.chat.id, message.from_user.id,
                                          f"Please read this link and summarise it for me: {url}",
                                          forced_tool=("link", url))
    await send_reply(message, res)


@router.message(Command("github"))
async def cmd_github(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    spec = (command.args or "").strip()
    if not spec:
        await message.answer("Use: /github owner/repo  (or owner/repo/path, or a github.com link)", parse_mode=None)
        return
    async with typing(bot, message.chat.id):
        res = await assistant.answer_text(message.chat.id, message.from_user.id,
                                          f"Tell me about this GitHub item: {spec}",
                                          forced_tool=("github", spec))
    await send_reply(message, res)


@router.message(Command("video"))
async def cmd_video(message: Message) -> None:
    await message.answer(persona.VIDEO_TEXT, parse_mode=None)


@router.message(Command("run"))
async def cmd_run(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    if not assistant.access.is_admin(message.from_user.id):
        return
    async with typing(bot, message.chat.id):
        out = await assistant.run_code(message.from_user.id, command.args or "", message.chat.id)
    await send_long(message, out or "(no output)")


@router.message(Command("sh"))
async def cmd_sh(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    if not assistant.access.is_admin(message.from_user.id):
        return
    async with typing(bot, message.chat.id):
        out = await assistant.run_shell(message.from_user.id, message.chat.id, command.args or "")
    await send_long(message, out or "(no output)")


@router.message(Command("files"))
async def cmd_files(message: Message, command: CommandObject, bot, assistant: Assistant) -> None:
    if not assistant.access.is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    async with typing(bot, message.chat.id):
        if arg.startswith("/") and "." in arg.rsplit("/", 1)[-1]:
            res = await assistant.sandbox_get(message.from_user.id, message.chat.id, arg)
            await send_reply(message, res)
            return
        out = await assistant.sandbox_files(message.from_user.id, message.chat.id, arg)
    await send_long(message, out)


@router.message(F.document)
async def on_document(message: Message, bot, assistant: Assistant, bot_username: str, bot_id: int) -> None:
    if not _addressed(message, bot_username, bot_id):
        return
    doc = message.document
    if doc.file_size and doc.file_size > 8_000_000:
        await message.answer("That file is over 8 MB, send a smaller one 🙏", parse_mode=None)
        return
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    if assistant.access.is_admin(message.from_user.id) and assistant.sandbox_mode() == "e2b" \
            and ((message.caption or "").strip().lower().startswith(("sandbox", "/sandbox", "/upload"))
                 or (doc.file_name or "").lower().rsplit(".", 1)[-1] not in ("txt", "md", "csv", "docx", "xlsx", "pdf")):
        path = await assistant.sandbox_put(message.from_user.id, message.chat.id, doc.file_name or "upload.bin", buf.getvalue())
        await message.answer(f"Uploaded to the sandbox: {path}" if path else "Upload to the sandbox failed 🙏", parse_mode=None)
        return
    caption = _strip_mention(message.caption or "", bot_username)
    if not caption.strip():
        # file first, instruction in the next message: remember the file for 20 minutes
        _PENDING_DOCS[message.chat.id] = (time.monotonic(), doc.file_name or "file.txt", buf.getvalue())
        async with typing(bot, message.chat.id):
            res = await assistant.edit_document(message.chat.id, message.from_user.id, doc.file_name or "file.txt", buf.getvalue(), "")
        res.text = (res.text or "").split("\n\n(Send the file again")[0] + "\n\nNow tell me in your next message what to change (I kept the file)."
        await send_reply(message, res)
        return
    async with typing(bot, message.chat.id):
        res = await assistant.edit_document(message.chat.id, message.from_user.id, doc.file_name or "file.txt",
                                            buf.getvalue(), caption)
    await send_reply(message, res)


@router.message(F.photo)
async def on_photo(message: Message, bot, assistant: Assistant, bot_username: str, bot_id: int) -> None:
    if not _addressed(message, bot_username, bot_id):
        return
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > 15_000_000:
        await message.answer("Ye photo bahut badi hai, chhoti bhejo please 🙏", parse_mode=None)
        return
    buf = io.BytesIO()
    await bot.download(photo, destination=buf)
    async with typing(bot, message.chat.id):
        res = await assistant.answer_photo(message.chat.id, message.from_user.id, buf.getvalue(),
                                           "image/jpeg", message.caption or "")
    await send_long(message, res.text)


@router.message(F.voice | F.audio)
async def on_voice(message: Message, bot, assistant: Assistant, bot_username: str, bot_id: int) -> None:
    if not _addressed(message, bot_username, bot_id):
        return
    media = message.voice or message.audio
    if media.file_size and media.file_size > 20_000_000:
        await message.answer("Ye audio bahut lamba/badi hai, chhota bhejo 🙏", parse_mode=None)
        return
    mime = getattr(media, "mime_type", None) or "audio/ogg"
    filename = "voice.ogg" if message.voice else (getattr(media, "file_name", None) or "audio.mp3")
    buf = io.BytesIO()
    await bot.download(media, destination=buf)
    async with typing(bot, message.chat.id):
        res = await assistant.answer_voice(message.chat.id, message.from_user.id, buf.getvalue(), filename, mime)
    await send_long(message, res.text)


_REMINDERS: set = set()
_PENDING_DOCS: dict = {}  # chat_id -> (time, file name, bytes)


async def _remind(bot, chat_id: int, secs: int, what: str) -> None:
    await asyncio.sleep(secs)
    try:
        await bot.send_message(chat_id, f"⏰ Reminder: {what}", parse_mode=None)
    except Exception:  # noqa: BLE001
        log.warning("could not deliver a reminder")


@router.message(F.text)
async def on_text(message: Message, bot, assistant: Assistant, bot_username: str, bot_id: int) -> None:
    if not _addressed(message, bot_username, bot_id):
        return
    text = _strip_mention(message.text or "", bot_username)
    if not text:
        return
    if text.strip() == NEW_CHAT_LABEL:
        _PENDING_DOCS.pop(message.chat.id, None)
        await message.answer(assistant.reset(message.chat.id) + "\nNaya chat shuru 🆕", parse_mode=None, reply_markup=NEW_CHAT_KB if message.chat.type == "private" else None)
        return
    pend = _PENDING_DOCS.get(message.chat.id)
    if pend and time.monotonic() - pend[0] < 1200 and len(text) >= 4 and not text.lstrip().startswith("/"):
        _PENDING_DOCS.pop(message.chat.id, None)
        async with typing(bot, message.chat.id):
            res = await assistant.edit_document(message.chat.id, message.from_user.id, pend[1], pend[2], text)
        await send_reply(message, res)
        return
    if pend and time.monotonic() - pend[0] >= 1200:
        _PENDING_DOCS.pop(message.chat.id, None)
    rem = extras.reminder_request(text)
    if rem:
        secs, what = rem
        if len(_REMINDERS) >= 200:
            await message.answer("Too many reminders waiting right now, try later 🙏", parse_mode=None)
            return
        task = asyncio.create_task(_remind(bot, message.chat.id, secs, what))
        _REMINDERS.add(task)
        task.add_done_callback(_REMINDERS.discard)
        await message.answer(f"Okay ✅ I will remind you in {secs // 60 if secs >= 60 else secs} {'min' if secs >= 60 else 'sec'}: {what}\n(If the bot restarts before that, the reminder is lost.)", parse_mode=None)
        return
    async with typing(bot, message.chat.id):
        res = await assistant.answer_text(message.chat.id, message.from_user.id, text)
    await send_reply(message, res)
