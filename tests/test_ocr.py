import io
import pymupdf as fitz
from ai_bot.photoedit import photo_question, OCR_PROMPT


def test_photo_question():
    assert "text" in photo_question("").lower()
    assert photo_question("text nikalo").startswith(OCR_PROMPT)
    assert photo_question("is mein kitne log hain") == "is mein kitne log hain"


def test_pdf_renders_to_jpeg():
    d = fitz.open(); p = d.new_page(); p.insert_text((72, 72), "hello")
    jpg = p.get_pixmap(dpi=130).tobytes("jpeg")
    assert jpg[:2] == b"\xff\xd8"
