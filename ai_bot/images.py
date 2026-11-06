"""Image generation. Pollinations needs no key but allows one picture per 45 s per address (anonymous tier);
Cloudflare Workers AI (flux-1-schnell) is used as a second source when its account id and token are set."""
from __future__ import annotations

import asyncio
import base64
import random
import re
from urllib.parse import quote

import httpx


class ImageUnavailable(Exception):
    pass


class ImageGen:
    BASE = "https://image.pollinations.ai/prompt/"
    MAX_WAIT = 50  # seconds we are willing to wait for the free slot

    def __init__(self, client: httpx.AsyncClient, cf_account: str = "", cf_token: str = "") -> None:
        self.client = client
        self.cf_account, self.cf_token = cf_account, cf_token

    async def _pollinations(self, prompt: str) -> bytes | None:
        url = f"{self.BASE}{quote(prompt, safe='')}"
        for attempt in range(2):
            params = {"width": 1024, "height": 1024, "nologo": "true", "seed": random.randint(1, 10**6)}
            try:
                r = await self.client.get(url, params=params, timeout=90)
            except httpx.HTTPError:
                return None
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 2000:
                return r.content
            if r.status_code in (402, 429) and attempt == 0:
                m = re.search(r"[Rr]etry after (\d+)s", r.text)
                wait = int(m.group(1)) + 1 if m else 20
                if wait > self.MAX_WAIT:
                    return None
                await asyncio.sleep(wait)
                continue
            return None
        return None

    async def _cloudflare(self, prompt: str) -> bytes | None:
        if not (self.cf_account and self.cf_token):
            return None
        try:
            r = await self.client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                headers={"Authorization": f"Bearer {self.cf_token}"}, json={"prompt": prompt, "steps": 4}, timeout=90)
            if r.status_code == 200:
                b64 = (r.json().get("result") or {}).get("image")
                if b64:
                    return base64.b64decode(b64)
        except (httpx.HTTPError, ValueError):
            pass
        return None

    async def generate(self, prompt: str) -> bytes:
        prompt = " ".join(prompt.split())[:600]
        if not prompt:
            raise ImageUnavailable("empty prompt")
        for source in (self._pollinations, self._cloudflare):
            data = await source(prompt)
            if data:
                return data
        raise ImageUnavailable("image service is busy")
