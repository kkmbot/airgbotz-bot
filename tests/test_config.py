from ai_bot.config import Settings, parse_ids


def test_admin_ids_parse_with_commas_and_spaces_and_junk():
    assert parse_ids("111111111, 42 ;abc") == frozenset({111111111, 42})
    assert parse_ids("") == frozenset()


def test_from_env_defaults_are_admin_only():
    s = Settings.from_env({})
    assert s.allow_all is False
    assert s.admin_ids == frozenset()
    assert s.provider_order == ("gemini", "groq", "openrouter", "cloudflare", "ollama")


def test_allow_all_true_values():
    assert Settings.from_env({"ALLOW_ALL": "true"}).allow_all is True
    assert Settings.from_env({"ALLOW_ALL": "0"}).allow_all is False


def test_repr_never_shows_secret_values():
    s = Settings.from_env({"GEMINI_API_KEY": "AIzaSECRETVALUE12345", "TELEGRAM_BOT_TOKEN": "123:TOKENVALUE"})
    text = repr(s)
    assert "AIzaSECRETVALUE" not in text and "TOKENVALUE" not in text
    assert "AIzaSECRETVALUE12345" in s.secrets()
