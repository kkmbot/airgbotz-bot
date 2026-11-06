"""Fallback chain: try providers in order; a 429 puts that provider on cooldown; errors fall through."""
from __future__ import annotations

import logging
import time
from typing import Callable

from .providers import BaseProvider, ProviderError
from .store import Store

log = logging.getLogger(__name__)


class AllProvidersDown(Exception):
    pass


class Chain:
    def __init__(self, providers: list[BaseProvider], store: Store | None = None,
                 cooldown_seconds: int = 600, auth_cooldown_seconds: int = 3600,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.providers = providers
        self.store = store
        self.cooldown_seconds = cooldown_seconds
        self.auth_cooldown_seconds = auth_cooldown_seconds
        self.clock = clock
        self._until: dict[str, float] = {}
        self.last_provider: str | None = None
        self.day_fn: Callable[[], str] = lambda: time.strftime("%Y-%m-%d")

    def cooldown_left(self, name: str) -> float:
        return max(0.0, self._until.get(name, 0.0) - self.clock())

    def _start_cooldown(self, name: str, seconds: float) -> None:
        self._until[name] = max(self._until.get(name, 0.0), self.clock() + seconds)

    def _record(self, name: str, kind: str) -> None:
        if self.store is not None:
            self.store.bump_usage(self.day_fn(), name, kind)

    def _on_error(self, p: BaseProvider, e: ProviderError) -> None:
        self._record(p.name, "err")
        if e.status == 429:
            wait = max(self.cooldown_seconds, e.retry_after or 0)
            self._start_cooldown(p.name, min(wait, 3600))
            log.warning("%s rate-limited; cooling down %ds", p.name, int(wait))
        elif e.status in (401, 402, 403):
            self._start_cooldown(p.name, self.auth_cooldown_seconds)
            log.warning("%s refused (status %s); off for a while - check its key/billing", p.name, e.status)
        else:
            log.warning("%s failed (%s); trying next", p.name, e.status or e.message)

    async def generate(self, messages: list[dict], system: str, *, image: bytes | None = None,
                       mime: str | None = None, vision: bool = False) -> tuple[str, str]:
        """Return (text, provider_name) from the first provider that answers."""
        for p in self.providers:
            if not p.configured():
                continue
            if vision and not p.supports_vision:
                continue
            if self.cooldown_left(p.name) > 0:
                continue
            try:
                text = (await p.chat(messages, system, image=image, mime=mime) or "").strip()
                if not text:
                    raise ProviderError(p.name, None, "empty reply")
            except ProviderError as e:
                self._on_error(p, e)
                continue
            except Exception:  # never let one provider's bug take the whole chain down
                log.exception("%s crashed inside the adapter; trying next", p.name)
                self._record(p.name, "err")
                continue
            self._record(p.name, "ok")
            self.last_provider = p.name
            return text, p.name
        raise AllProvidersDown("no provider answered")

    def status(self) -> list[tuple[str, str]]:
        out = []
        for p in self.providers:
            if not p.configured():
                out.append((p.name, "off (not set up)"))
            elif self.cooldown_left(p.name) > 0:
                out.append((p.name, f"cooling down ({int(self.cooldown_left(p.name))}s left)"))
            else:
                out.append((p.name, "ready"))
        return out
