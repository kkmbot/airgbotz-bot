"""Entry point: python -m ai_bot  (long polling; no webhook, no open port needed)."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx
from aiogram import Bot, Dispatcher

from .access import Access
from .chain import Chain
from .config import Settings, usable_db_path
from .health import start_health_server
from .core import Assistant
from .providers import Groq, build_providers
from .store import Store
from .telegram_app import AccessGate, router
from .tools import SearchClient, Weather


class GroqSTT:
    """Voice-to-text on Groq Whisper. If no key is set, the bot says so politely instead."""

    def __init__(self, groq: Groq, model: str) -> None:
        self.groq = groq
        self.model = model

    def configured(self) -> bool:
        return self.groq.configured()

    async def transcribe(self, audio: bytes, filename: str, mime: str) -> str:
        return await self.groq.transcribe(audio, filename, mime, self.model)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # aiogram logs request URLs at debug, and those URLs carry the bot token: keep it quiet.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings.from_env()
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not settings.admin_ids:
        logging.warning("ADMIN_IDS is empty: nobody can use the bot until you set it")

    health = None
    port = (os.environ.get("PORT") or "").strip()
    if port.isdigit():
        health = await start_health_server(int(port))
        logging.info("health endpoint listening on port %s", port)

    store = Store(usable_db_path(settings.db_path))
    access = Access(settings, store)
    http = httpx.AsyncClient(timeout=settings.request_timeout)
    providers = build_providers(settings, http)
    chain = Chain(providers, store=store, cooldown_seconds=settings.cooldown_seconds)
    groq = next((p for p in providers if isinstance(p, Groq)), None) or Groq(http, "", "", [])
    assistant = Assistant(settings, store, chain, access,
                          SearchClient(http, settings.tavily_api_key, settings.serper_api_key),
                          Weather(http), stt=GroqSTT(groq, settings.groq_whisper_model))

    bot = Bot(token=settings.telegram_token)
    gate = AccessGate()
    dp = Dispatcher()
    dp.message.outer_middleware(gate)
    dp.include_router(router)
    me = await bot.get_me()
    logging.info("bot is up (admins configured: %d, allow_all: %s)", len(settings.admin_ids), settings.allow_all)
    try:
        await dp.start_polling(bot, assistant=assistant, access=access, bot_username=me.username or "",
                               bot_id=me.id, allowed_updates=["message"])
    finally:
        await bot.session.close()
        await http.aclose()
        store.close()
        if health is not None:
            health.close()


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.environ.get("ENV_FILE", ".env"))
    except ImportError:
        pass
    asyncio.run(run())


if __name__ == "__main__":
    main()
