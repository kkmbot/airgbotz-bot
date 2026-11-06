import asyncio

import pytest

from ai_bot import docs, sandbox
from ai_bot.githubread import parse_spec
from ai_bot.web import UnsafeURL, check_url, html_to_text


def run(c):
    return asyncio.run(c)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/x", "http://10.0.0.5/", "http://169.254.169.254/latest",
                                  "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://user:pw@example.com/"])
def test_unsafe_urls_blocked(url):
    with pytest.raises(Exception) as e:
        run(check_url(url))
    assert isinstance(e.value, UnsafeURL) or "host not found" in str(e.value)


def test_html_text_strips_scripts():
    t, body = html_to_text("<html><title>Hi &amp; bye</title><script>alert(1)</script><style>x{}</style><p>Hello</p><div>World</div></html>")
    assert t == "Hi & bye" and "alert" not in body and "Hello" in body and "World" in body


def test_github_spec():
    assert parse_spec("kkmbot/airgbotz-bot")["repo"] == "airgbotz-bot"
    p = parse_spec("https://github.com/a/b/blob/main/src/x.py")
    assert p["ref"] == "main" and p["path"] == "src/x.py"
    p = parse_spec("github.com/a/b/issues/7")
    assert p["issues"] and p["number"] == 7
    assert parse_spec("not a repo") is None


@pytest.mark.parametrize("fmt", docs.FORMATS)
def test_doc_roundtrip(fmt):
    name, data = docs.make_doc(fmt, "Plan", "# Head\n- one\n- two\n\nName | Qty\nApple | 3")
    assert name.endswith("." + fmt) and data
    _, text = docs.extract_text(name, data)
    assert "Apple" in text


def test_doc_bad_format():
    with pytest.raises(docs.DocError):
        docs.make_doc("exe", "x", "y")


def test_sandbox_strips_env_and_times_out(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    out = run(sandbox.run_python("import os;print(sorted(os.environ))"))
    assert "GEMINI" not in out and "secret" not in out
    assert "stopped after" in run(sandbox.run_python("while True: pass", timeout=2))
