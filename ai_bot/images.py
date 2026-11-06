"""Image generation. Pollinations needs no key but allows one picture per 45 s per address (anonymous tier);
Cloudflare Workers AI (flux-1-schnell) is used as a second source when its account id and token are set."""
from __future__ import annotations

import asyncio
import base64
import random
import re
from urllib.parse import quote

import httpx


def _trim_watermark(data: bytes) -> bytes:
    """The free tier stamps a small badge in the bottom-right corner; crop the bottom 6% away."""
    import io
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        out = io.BytesIO()
        img.crop((0, 0, w, int(h * 0.94))).save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - keep the original if anything is odd
        return data


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
                return _trim_watermark(r.content)
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


def add_caption(data: bytes, text: str) -> bytes:
    """Write exact words on a picture with a real font (image models garble letters). Returns JPEG bytes."""
    import io
    import os
    from PIL import Image, ImageDraw, ImageFont

    text = " ".join(text.split())[:60]
    if not text:
        return data
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    fonts = os.path.join(os.path.dirname(__file__), "fonts")
    name = "NotoSansDevanagari-Regular.ttf" if re.search(r"[\u0900-\u097F]", text) else "DejaVuSans.ttf"
    path = os.path.join(fonts, name)
    size = int(h * 0.11)
    while size > 14:
        font = ImageFont.truetype(path, size)
        if font.getlength(text) <= w * 0.88:
            break
        size -= 4
    pad = int(size * 0.55)
    band_h = size + pad * 2
    top = h - band_h - int(h * 0.04)
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    d.rectangle([0, top, w, top + band_h], fill=(0, 0, 0, 165))
    d.text((w / 2, top + band_h / 2), text, font=font, fill=(255, 255, 255, 255), anchor="mm",
           stroke_width=max(1, size // 28), stroke_fill=(255, 255, 255, 255))
    out = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return buf.getvalue()
