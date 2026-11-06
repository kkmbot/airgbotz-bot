"""Provider adapters. Each one is a thin HTTP client: keys go in headers, never in URLs or logs."""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """A provider said no (status set) or never answered (status None: timeout / network / bad body)."""

    def __init__(self, provider: str, status: int | None = None, message: str = "",
                 retry_after: float | None = None) -> None:
        super().__init__(f"{provider}: {status or 'no-status'} {message}")
        self.provider = provider
        self.status = status
        self.message = message
        self.retry_after = retry_after


class BaseProvider:
    name = "base"
    supports_vision = False
    timeout = 45.0

    def __init__(self, client: httpx.AsyncClient, secrets: list[str] | None = None) -> None:
        self.client = client
        self._secrets = [s for s in (secrets or []) if s]

    def configured(self) -> bool:
        return True

    async def chat(self, messages: list[dict], system: str, image: bytes | None = None,
                   mime: str | None = None) -> str:
        raise NotImplementedError

    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        return text[:200]

    async def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        kw.setdefault("timeout", self.timeout)
        try:
            r = await self.client.request(method, url, **kw)
        except httpx.TimeoutException:
            raise ProviderError(self.name, None, "timeout")
        except httpx.HTTPError:
            raise ProviderError(self.name, None, "network error")
        if r.status_code >= 400:
            retry_after = None
            ra = r.headers.get("retry-after")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
            raise ProviderError(self.name, r.status_code, self._scrub(r.text), retry_after)
        return r

    @staticmethod
    def _json(r: httpx.Response, provider: str) -> Any:
        try:
            return r.json()
        except ValueError:
            raise ProviderError(provider, None, "bad json")


class Gemini(BaseProvider):
    name = "gemini"
    supports_vision = True
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, client, api_key: str, model: str, secrets=None) -> None:
        super().__init__(client, secrets)
        self.api_key = api_key
        self.model = model

    def configured(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages, system, image=None, mime=None) -> str:
        contents: list[dict] = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            if not contents and role == "model":
                continue  # Gemini wants the conversation to open with a user turn
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "hi"}]}]
        if image is not None:
            contents[-1]["parts"].append({
                "inline_data": {"mime_type": mime or "image/jpeg",
                                "data": base64.b64encode(image).decode("ascii")}})
        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
        }
        r = await self._request("POST", f"{self.BASE}/models/{self.model}:generateContent",
                                headers={"x-goog-api-key": self.api_key}, json=body)
        data = self._json(r, self.name)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            raise ProviderError(self.name, None, "no candidate in reply")


class OpenAICompatible(BaseProvider):
    """Groq, OpenRouter and anything else speaking /chat/completions."""
    base_url = ""

    def __init__(self, client, api_key: str, model: str, secrets=None) -> None:
        super().__init__(client, secrets)
        self.api_key = api_key
        self.model = model

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def chat(self, messages, system, image=None, mime=None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        r = await self._request("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        data = self._json(r, self.name)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError(self.name, None, "no choices in reply")


class Groq(OpenAICompatible):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"

    async def transcribe(self, audio: bytes, filename: str, mime: str, model: str) -> str:
        r = await self._request(
            "POST", f"{self.base_url}/audio/transcriptions", headers=self._headers(),
            files={"file": (filename, audio, mime)}, data={"model": model, "response_format": "json"})
        data = self._json(r, self.name)
        return (data.get("text") or "").strip()


class OpenRouter(OpenAICompatible):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "X-Title": "Sathi bot"}


class Nvidia(OpenAICompatible):
    """NVIDIA NIM free API (build.nvidia.com): GLM 5.3 Flash and other open models, about 40 requests a minute on the free tier."""
    name = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"
    timeout = 60.0


class Cloudflare(BaseProvider):
    name = "cloudflare"

    def __init__(self, client, account_id: str, api_token: str, model: str, secrets=None) -> None:
        super().__init__(client, secrets)
        self.account_id = account_id
        self.api_token = api_token
        self.model = model

    def configured(self) -> bool:
        return bool(self.account_id and self.api_token)

    async def chat(self, messages, system, image=None, mime=None) -> str:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        body = {"messages": [{"role": "system", "content": system}] + messages, "max_tokens": 1024}
        r = await self._request("POST", url, headers={"Authorization": f"Bearer {self.api_token}"}, json=body)
        data = self._json(r, self.name)
        try:
            return data["result"]["response"] or ""
        except (KeyError, TypeError):
            raise ProviderError(self.name, None, "no result in reply")


class Ollama(BaseProvider):
    """Local model on the user's own server. CPU-only is slow: the timeout is long on purpose."""
    name = "ollama"
    timeout = 180.0

    def __init__(self, client, url: str, model: str, secrets=None) -> None:
        super().__init__(client, secrets)
        self.url = url
        self.model = model

    def configured(self) -> bool:
        return bool(self.url)

    async def chat(self, messages, system, image=None, mime=None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": {"num_predict": 400},
        }
        r = await self._request("POST", f"{self.url}/api/chat", json=body)
        data = self._json(r, self.name)
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError):
            raise ProviderError(self.name, None, "no message in reply")


def build_providers(settings: Settings, client: httpx.AsyncClient) -> list[BaseProvider]:
    s = settings.secrets()
    registry = {
        "gemini": lambda: Gemini(client, settings.gemini_api_key, settings.gemini_model, s),
        "groq": lambda: Groq(client, settings.groq_api_key, settings.groq_model, s),
        "nvidia": lambda: Nvidia(client, settings.nvidia_api_key, settings.nvidia_model, s),
        "openrouter": lambda: OpenRouter(client, settings.openrouter_api_key, settings.openrouter_model, s),
        "cloudflare": lambda: Cloudflare(client, settings.cloudflare_account_id, settings.cloudflare_api_token,
                                         settings.cloudflare_model, s),
        "ollama": lambda: Ollama(client, settings.ollama_url, settings.ollama_model, s),
    }
    out: list[BaseProvider] = []
    for name in settings.provider_order:
        if name in registry:
            out.append(registry[name]())
        else:
            log.warning("unknown provider name in PROVIDER_ORDER, skipped")
    return out
