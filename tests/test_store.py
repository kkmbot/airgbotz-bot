from ai_bot.store import Store


def test_recent_window_is_chronological_and_limited():
    s = Store(":memory:")
    for i in range(10):
        s.add_message(1, "user" if i % 2 == 0 else "assistant", f"m{i}")
    got = [m["content"] for m in s.recent(1, 4)]
    assert got == ["m6", "m7", "m8", "m9"]


def test_chats_are_separate():
    s = Store(":memory:")
    s.add_message(1, "user", "a")
    s.add_message(2, "user", "b")
    assert [m["content"] for m in s.recent(2, 10)] == ["b"]


def test_summary_fold_deletes_only_old_rows():
    s = Store(":memory:")
    for i in range(6):
        s.add_message(1, "user", f"m{i}")
    old = s.oldest(1, 3)
    s.set_summary(1, "likes tea")
    s.delete_messages_upto(1, old[-1]["id"])
    assert s.count_messages(1) == 3
    assert s.get_summary(1) == "likes tea"


def test_reset_clears_messages_and_summary():
    s = Store(":memory:")
    s.add_message(1, "user", "hi")
    s.set_summary(1, "x")
    s.reset_chat(1)
    assert s.count_messages(1) == 0 and s.get_summary(1) == ""


def test_usage_counts_per_provider_and_kind():
    s = Store(":memory:")
    s.bump_usage("2026-01-01", "gemini", "ok")
    s.bump_usage("2026-01-01", "gemini", "ok")
    s.bump_usage("2026-01-01", "gemini", "err")
    rows = {(r["provider"], r["kind"]): r["count"] for r in s.usage_for_day("2026-01-01")}
    assert rows == {("gemini", "ok"): 2, ("gemini", "err"): 1}


def test_user_daily_counter():
    s = Store(":memory:")
    assert s.get_user_day("d", 5) == 0
    s.bump_user_day("d", 5)
    s.bump_user_day("d", 5)
    assert s.get_user_day("d", 5) == 2 and s.get_user_day("e", 5) == 0


def test_allowlist_and_meta_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / "bot.sqlite3")
    s = Store(path)
    s.allow(7)
    s.set_meta("k", "v")
    s.close()
    s2 = Store(path)
    assert s2.is_allowed(7) and s2.get_meta("k") == "v" and s2.get_meta("none", "d") == "d"
