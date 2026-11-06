from ai_bot.core import Assistant
from ai_bot.store import Store


def mk():
    a = Assistant.__new__(Assistant)
    a.store = Store()
    return a


def test_notes_roundtrip():
    a = mk()
    assert "Noted" in a.memory_command(1, "remember my exam is on 5 Dec")
    assert "Noted" in a.memory_command(1, "Yaad rakho: mera naam Mavi hai")
    out = a.memory_command(1, "what do you remember")
    assert "exam" in out and "Mavi" in out
    assert a.memory_command(2, "my notes").startswith("Nothing")
    assert a.memory_command(1, "forget my notes") == "Notes cleared \u2705"
    assert a.memory_command(1, "hello how are you") is None
    assert a.memory_command(1, "remember") is None
