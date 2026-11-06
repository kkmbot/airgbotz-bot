from ai_bot.docs import extract_text, make_doc


def test_pdf_with_hindi_and_english_roundtrip():
    n, b = make_doc("pdf", "T", "Hello world\nनमस्ते दुनिया", with_title=False)
    fmt, txt = extract_text(n, b)
    assert fmt == "pdf" and "Hello world" in txt and "नमस्त" in txt and "\x08" not in txt
