"""Settings come from environment variables only (Coolify sets them). Secrets never get printed."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Mapping

log = logging.getLogger(__name__)

DEFAULT_ORDER = ("gemini", "groq", "openrouter", "cloudflare", "ollama")


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(v: str | None, default: int) -> int:
    try:
        return int(str(v).strip()) if v not in (None, "") else default
    except ValueError:
        log.warning("bad integer setting ignored")
        return default


def parse_ids(raw: str | None) -> frozenset[int]:
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part.lstrip("-").isdigit():
            out.add(int(part))
        else:
            log.warning("ignored a non-numeric ADMIN_IDS entry")
    return frozenset(out)


def usable_db_path(path: str) -> str:
    """Use the configured database path if its folder can be created and written.

    Hosts without a persistent volume often refuse /data. Fall back to ./data so the bot still starts;
    chat memory then lives in the container's own disk and resets on redeploy.
    """
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(folder, exist_ok=True)
        if os.access(folder, os.W_OK):
            return path
    except OSError:
        pass
    fallback = os.path.join(os.getcwd(), "data", "bot.sqlite3")
    log.warning("database folder %s is not writable; using %s instead", folder, fallback)
    return fallback


@dataclass(frozen=True, repr=False)
class Settings:
    telegram_token: str = ""
    admin_ids: frozenset[int] = frozenset()
    allow_all: bool = False
    db_path: str = "/data/bot.sqlite3"
    bot_name: str = "Sathi"
    tz_offset_minutes: int = 330  # Asia/Kolkata
    memory_turns: int = 12        # last N user+assistant pairs sent to the model
    summary_every: int = 20       # fold old messages into the summary after this many extra
    daily_user_limit: int = 300   # per non-admin user, messages per local day
    cooldown_seconds: int = 600   # provider cooldown after a 429 (Retry-After wins if longer)
    request_timeout: float = 45.0
    provider_order: tuple[str, ...] = DEFAULT_ORDER
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-flash-lite-latest"
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    groq_whisper_model: str = "whisper-large-v3-turbo"
    openrouter_api_key: str = field(default="", repr=False)
    openrouter_model: str = "openrouter/free"
    cloudflare_account_id: str = field(default="", repr=False)
    cloudflare_api_token: str = field(default="", repr=False)
    cloudflare_model: str = "@cf/meta/llama-3.1-8b-instruct"
    ollama_url: str = ""
    ollama_model: str = "qwen2.5:3b-instruct"
    tavily_api_key: str = field(default="", repr=False)
    serper_api_key: str = field(default="", repr=False)
    github_token: str = field(default="", repr=False)
    enable_admin_sandbox: bool = False
    e2b_api_key: str = field(default="", repr=False)
    sandbox_backend: str = "auto"   # auto | e2b | local
    sandbox_timeout: int = 60
    sandbox_idle_seconds: int = 300
    sandbox_daily_limit: int = 200
    log_chat_id: int = 0          # optional channel/group that also receives the admin log lines
    heavy_daily_limit: int = 15   # images/docs/links/github per non-admin user per day

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        g = env.get
        order_raw = g("PROVIDER_ORDER") or ",".join(DEFAULT_ORDER)
        order = tuple(p.strip().lower() for p in order_raw.split(",") if p.strip())
        return cls(
            telegram_token=(g("TELEGRAM_BOT_TOKEN") or "").strip(),
            admin_ids=parse_ids(g("ADMIN_IDS")),
            allow_all=_bool(g("ALLOW_ALL"), False),
            db_path=g("DB_PATH") or "/data/bot.sqlite3",
            bot_name=g("BOT_NAME") or "Sathi",
            tz_offset_minutes=_int(g("BOT_TZ_OFFSET_MINUTES"), 330),
            memory_turns=_int(g("MEMORY_TURNS"), 12),
            summary_every=_int(g("SUMMARY_EVERY"), 20),
            daily_user_limit=_int(g("DAILY_USER_LIMIT"), 300),
            cooldown_seconds=_int(g("PROVIDER_COOLDOWN_SECONDS"), 600),
            request_timeout=float(_int(g("REQUEST_TIMEOUT_SECONDS"), 45)),
            provider_order=order,
            gemini_api_key=(g("GEMINI_API_KEY") or "").strip(),
            gemini_model=g("GEMINI_MODEL") or "gemini-flash-lite-latest",
            groq_api_key=(g("GROQ_API_KEY") or "").strip(),
            groq_model=g("GROQ_MODEL") or "openai/gpt-oss-120b",
            groq_whisper_model=g("GROQ_WHISPER_MODEL") or "whisper-large-v3-turbo",
            openrouter_api_key=(g("OPENROUTER_API_KEY") or "").strip(),
            openrouter_model=g("OPENROUTER_MODEL") or "openrouter/free",
            cloudflare_account_id=(g("CLOUDFLARE_ACCOUNT_ID") or "").strip(),
            cloudflare_api_token=(g("CLOUDFLARE_API_TOKEN") or "").strip(),
            cloudflare_model=g("CLOUDFLARE_MODEL") or "@cf/meta/llama-3.1-8b-instruct",
            ollama_url=(g("OLLAMA_URL") or "").strip().rstrip("/"),
            ollama_model=g("OLLAMA_MODEL") or "qwen2.5:3b-instruct",
            tavily_api_key=(g("TAVILY_API_KEY") or "").strip(),
            serper_api_key=(g("SERPER_API_KEY") or "").strip(),
            github_token=(g("GITHUB_TOKEN") or "").strip(),
            enable_admin_sandbox=_bool(g("ENABLE_ADMIN_SANDBOX"), False),
            e2b_api_key=(g("E2B_API_KEY") or "").strip(),
            sandbox_backend=(g("SANDBOX_BACKEND") or "auto").strip().lower(),
            sandbox_timeout=_int(g("SANDBOX_TIMEOUT_SECONDS"), 60),
            sandbox_idle_seconds=_int(g("SANDBOX_IDLE_SECONDS"), 300),
            sandbox_daily_limit=_int(g("SANDBOX_DAILY_LIMIT"), 200),
            log_chat_id=_int(g("LOG_CHAT_ID"), 0),
            heavy_daily_limit=_int(g("HEAVY_DAILY_LIMIT"), 15),
        )

    def secrets(self) -> list[str]:
        """Every secret value, so error text can be scrubbed before it is logged or shown."""
        vals = [self.telegram_token, self.gemini_api_key, self.groq_api_key, self.openrouter_api_key,
                self.cloudflare_api_token, self.tavily_api_key, self.serper_api_key, self.github_token, self.e2b_api_key]
        return [v for v in vals if v and len(v) >= 8]
