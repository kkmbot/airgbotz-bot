import asyncio, gzip, io, os, time
from types import SimpleNamespace as NS
from ai_bot.store import Store
from ai_bot import backup


def test_snapshot_roundtrip_and_reminders(tmp_path):
    s = Store(str(tmp_path / "a.sqlite3"))
    s.add_message(1, "user", "hello")
    s.add_note(1, "exam on 5 Dec")
    rid = s.add_reminder(1, time.time() + 60, "water")
    raw = s.snapshot()
    assert raw.startswith(b"SQLite format 3")
    p = tmp_path / "b.sqlite3"; p.write_bytes(raw)
    s2 = Store(str(p))
    assert s2.recent(1, 5)[0]["content"] == "hello" and s2.notes(1)[0]["text"] == "exam on 5 Dec"
    assert s2.reminders()[0]["what"] == "water"
    s2.del_reminder(rid); assert s2.reminders() == []


class FakeBot:
    def __init__(self, blob=None):
        self.blob = blob; self.sent = []; self.deleted = []; self.pinned = []
    async def get_chat(self, _id):
        if not self.blob: return NS(pinned_message=None)
        return NS(pinned_message=NS(message_id=5, document=NS(file_name=backup.FILENAME)))
    async def download(self, doc, destination):
        destination.write(self.blob)
    async def send_document(self, chat, f, caption=None, disable_notification=None):
        self.sent.append(f); return NS(message_id=9)
    async def pin_chat_message(self, *a, **k): self.pinned.append(a)
    async def delete_message(self, chat, mid): self.deleted.append(mid)


def test_backup_then_restore(tmp_path):
    s = Store(str(tmp_path / "a.sqlite3")); s.add_message(7, "user", "yaad rakhna")
    bot = FakeBot()
    b = backup.Backup(bot, 42, s)
    assert asyncio.run(b.run_once()) is True and bot.pinned
    assert asyncio.run(b.run_once()) is False  # nothing changed
    blob = bot.sent[0].data if hasattr(bot.sent[0], "data") else bot.sent[0].read()
    target = str(tmp_path / "fresh" / "x.sqlite3")
    res = asyncio.run(backup.restore(FakeBot(bytes(blob)), 42, target))
    assert res.startswith("restored")
    assert Store(target).recent(7, 3)[0]["content"] == "yaad rakhna"
    assert asyncio.run(backup.restore(FakeBot(bytes(blob)), 42, target)) == "skipped"  # never overwrites
