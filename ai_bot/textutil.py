"""Message splitting and light text cleanup. Telegram's hard limit is 4096 chars; we cut at 4000."""
from __future__ import annotations

import re

TELEGRAM_SAFE_LIMIT = 4000
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_reply(text: str) -> str:
    """Strip control characters and markdown bold markers the model sometimes adds."""
    text = _CTRL.sub("", text or "")
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def split_text(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> list[str]:
    """Split on paragraph, then line, then word boundaries. Every chunk is <= limit chars."""
    if limit < 50:
        raise ValueError("limit too small")
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit  # no whitespace at all: hard cut
        piece = rest[:cut].rstrip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
