from ai_bot.access import Access
from ai_bot.config import Settings
from ai_bot.store import Store

ADMIN = 111111111


def make(allow_all=False):
    s = Settings(admin_ids=frozenset({ADMIN}), allow_all=allow_all)
    return Access(s, Store(":memory:"))


def test_admin_always_allowed():
    a = make()
    assert a.is_allowed(ADMIN) and a.is_admin(ADMIN)


def test_stranger_denied_by_default():
    a = make()
    assert not a.is_allowed(42)


def test_allow_then_deny():
    a = make()
    a.allow(42)
    assert a.is_allowed(42)
    a.deny(42)
    assert not a.is_allowed(42)


def test_allow_all_switch_opens_to_everyone():
    a = make(allow_all=True)
    assert a.is_allowed(42)


def test_admin_is_not_admin_for_others():
    a = make()
    a.allow(42)
    assert not a.is_admin(42)
