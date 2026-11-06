import pytest
from ai_bot.textutil import clean_reply, split_text


def test_short_text_is_one_chunk():
    assert split_text("hello") == ["hello"]


def test_empty_gives_no_chunks():
    assert split_text("   ") == []


@pytest.mark.parametrize("size", [3999, 4000, 4001, 9000, 25000])
def test_every_chunk_within_limit_and_content_kept(size):
    words = ("namaste dost kaise ho " * (size // 10 + 1))[:size]
    text = "\n\n".join(words[i:i + 300] for i in range(0, len(words), 300))
    chunks = split_text(text)
    assert all(len(c) <= 4000 for c in chunks)
    assert " ".join(text.split()) == " ".join(" ".join(chunks).split())


def test_no_whitespace_hard_cut_still_within_limit():
    chunks = split_text("x" * 9000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == "x" * 9000


def test_prefers_paragraph_boundary():
    text = "a" * 3000 + "\n\n" + "b" * 2000
    chunks = split_text(text)
    assert chunks[0] == "a" * 3000
    assert chunks[1] == "b" * 2000


def test_clean_reply_strips_bold_and_control_chars():
    assert clean_reply("**hi**\x07 there") == "hi there"
