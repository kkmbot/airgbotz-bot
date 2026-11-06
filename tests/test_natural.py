from types import SimpleNamespace as NS

from ai_bot.core import Assistant


def mk(admin):
    a = Assistant.__new__(Assistant)
    a.access = NS(is_admin=lambda uid: admin)
    return a


def test_plain_words_replace_commands():
    a = mk(False)
    assert a.natural_command(1, "reset") == "reset"
    assert a.natural_command(1, "Please clear chat!") == "reset"
    assert a.natural_command(1, "which model?") == "model"
    assert a.natural_command(1, "usage") is None          # admin only
    assert mk(True).natural_command(1, "usage") == "usage"
    assert a.natural_command(1, "how do I reset my router password and what then") is None
