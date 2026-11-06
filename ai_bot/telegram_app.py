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
from aiogram.types import Message

from . import persona
from .core import Assistant
from .access import Access
from .textutil import split_text

log = logging.getLogger(__name__)
router = Router(name="main")


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


async def send_long(message: Message, text: str) -> None:
    chunks = split_text(text) or ["(khaali jawab, dobara poochho)"]
    for chunk in chunks:
        for attempt in range(2):
            try:
                await message.answer(chunk, parse_mode=None)
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
    await message.answer(persona.START_TEXT.format(name=assistant.settings.bot_name), parse_mode=None)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(persona.HELP_TEXT, parse_mode=None)


@router.message(Command("reset"))
async def cmd_reset(message: Message, assistant: Assistant) -> None:
    await message.answer(assistant.reset(message.chat.id), parse_mode=None)


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


@router.message(F.text)
async def on_text(message: Message, bot, assistant: Assistant, bot_username: str, bot_id: int) -> None:
    if not _addressed(message, bot_username, bot_id):
        return
    text = _strip_mention(message.text or "", bot_username)
    if not text:
        return
    async with typing(bot, message.chat.id):
        res = await assistant.answer_text(message.chat.id, message.from_user.id, text)
    await send_long(message, res.text)
