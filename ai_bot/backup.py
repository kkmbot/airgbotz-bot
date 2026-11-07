"""Keeps the bot's memory across restarts without any disk or paid database.

The host wipes local files on every redeploy. So the bot sends a compressed copy of its own database to the admin's
private chat (silently, pinned, replacing the previous copy) and, on a fresh start, reads the pinned copy back."""
from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import re

from aiogram.types import BufferedInputFile

log = logging.getLogger(__name__)
FILENAME = "airgbotz-memory-backup.sqlite3.gz"
TAG = re.compile(r"\s*🗂bk:(\d+)")
MAX_BYTES = 15_000_000
EVERY_SECONDS = 90


async def restore(bot, admin_id, db_path: str) -> str:
    """Call BEFORE the Store opens. Only restores when there is no local database yet.
    admin_id may be one chat id or a list tried in order (e.g. the storage group, then the admin chat)."""
    ids = [i for i in (admin_id if isinstance(admin_id, (list, tuple)) else [admin_id]) if i]
    if not ids or os.path.exists(db_path):
        return "skipped"
    last = "no backup found"
    for cid in ids:
        last = await _restore_from(bot, cid, db_path)
        if last.startswith("restored"):
            return last
    return last


async def _restore_from(bot, admin_id: int, db_path: str) -> str:
    try:
        chat = await bot.get_chat(admin_id)
        doc = None
        m = TAG.search(chat.description or "")
        if m:  # group mode: the message id lives in the group description, nothing is pinned
            fwd = await bot.forward_message(admin_id, admin_id, int(m.group(1)), disable_notification=True)
            doc = fwd.document
            try:
                await bot.delete_message(admin_id, fwd.message_id)
            except Exception:  # noqa: BLE001
                pass
        elif chat.pinned_message and chat.pinned_message.document:
            doc = chat.pinned_message.document
        if not doc or doc.file_name != FILENAME:
            return "no backup found"
        buf = io.BytesIO()
        await bot.download(doc, destination=buf)
        raw = gzip.decompress(buf.getvalue())
        if not raw.startswith(b"SQLite format 3"):
            return "backup unreadable"
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with open(db_path, "wb") as f:
            f.write(raw)
        return f"restored {len(raw)} bytes"
    except Exception as e:  # noqa: BLE001 - a missing backup must never stop the bot from starting
        log.warning("backup restore failed: %s", type(e).__name__)
        return "restore failed"


class Backup:
    def __init__(self, bot, admin_id: int, store) -> None:
        self.bot, self.admin_id, self.store = bot, admin_id, store
        self._last_changes = -1
        self._last_msg_id = 0

    async def _find_previous(self) -> int:
        try:
            chat = await self.bot.get_chat(self.admin_id)
            m = TAG.search(chat.description or "")
            if m:
                return int(m.group(1))
            pm = chat.pinned_message
            if pm and pm.document and pm.document.file_name == FILENAME:
                return pm.message_id
        except Exception:  # noqa: BLE001
            pass
        return 0

    async def run_once(self, force: bool = False) -> bool:
        if not self.admin_id:
            return False
        changes = self.store.total_changes()
        if changes == self._last_changes and not force:
            return False
        data = gzip.compress(await asyncio.to_thread(self.store.snapshot), 6)
        if len(data) > MAX_BYTES:
            log.warning("backup too big, skipped")
            return False
        if not self._last_msg_id:
            self._last_msg_id = await self._find_previous()
        msg = await self.bot.send_document(self.admin_id, BufferedInputFile(data, FILENAME),
                                           caption="🗂 Auto memory backup (do not delete, the bot reads this after a restart)",
                                           disable_notification=True)
        if self.admin_id < 0:  # group: remember the message id in the description (no pin, no "pinned" notices)
            try:
                chat = await self.bot.get_chat(self.admin_id)
                base = TAG.sub("", chat.description or "").rstrip()
                await self.bot.set_chat_description(self.admin_id, (base + "\n" if base else "") + f"🗂bk:{msg.message_id}")
            except Exception:  # noqa: BLE001
                log.warning("could not write the backup id into the group description")
        else:
            try:
                await self.bot.pin_chat_message(self.admin_id, msg.message_id, disable_notification=True)
            except Exception:  # noqa: BLE001
                log.warning("could not pin the backup")
        if self._last_msg_id:
            try:
                await self.bot.delete_message(self.admin_id, self._last_msg_id)
            except Exception:  # noqa: BLE001
                pass
        self._last_msg_id = msg.message_id
        self._last_changes = changes
        return True

    async def loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("backup failed: %s", type(e).__name__)
            await asyncio.sleep(EVERY_SECONDS)
