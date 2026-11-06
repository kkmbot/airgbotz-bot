from ai_bot.core import auto_tool


def test_links_are_read_without_commands():
    assert auto_tool("https://example.com/a?b=1 summarise") == ("link", "https://example.com/a?b=1")
    assert auto_tool("see https://github.com/kkmbot/airgbotz-bot.") == ("github", "https://github.com/kkmbot/airgbotz-bot")
    assert auto_tool("hello there") is None
    assert auto_tool("x " * 400 + "https://example.com") is None
