import asyncio

import pymupdf
import pytest

from ai_bot import pdfedit
from ai_bot.core import Assistant
from ai_bot.config import Settings
from ai_bot.store import Store
from ai_bot.access import Access
from tests.test_agent import ScriptChain


def sample():
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((60, 90), "Hello wrold", fontsize=14)
    p.insert_text((60, 160), "Keep this line exactly.", fontsize=11)
    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    img.set_rect(img.irect, (0, 200, 0))
    p.insert_image(pymupdf.Rect(300, 50, 340, 90), pixmap=img)
    return d.tobytes()


def test_only_changed_block_changes_and_image_stays():
    data = sample()
    bl = pdfedit.list_blocks(data)
    ed = {b["id"]: "Hello world" for b in bl if "wrold" in b["text"]}
    out = pdfedit.apply_edits(data, ed)
    d = pymupdf.open(stream=out)
    txt = d[0].get_text()
    assert "Hello world" in txt and "wrold" not in txt and "Keep this line exactly." in txt
    assert len(d[0].get_images()) == 1


def test_scanned_pdf_rejected():
    d = pymupdf.open(); d.new_page()
    with pytest.raises(pdfedit.PdfEditError):
        pdfedit.list_blocks(d.tobytes())


def test_assistant_edits_pdf_in_place(tmp_path):
    data = sample()
    s = Settings(admin_ids=frozenset({1}), db_path=str(tmp_path / "a.db"))
    st = Store(s.db_path)
    bl = pdfedit.list_blocks(data)
    bid = [b["id"] for b in bl if "wrold" in b["text"]][0]
    a = Assistant(s, st, ScriptChain(['{"%d": "Hello world"}' % bid]), Access(s, st), None, None)
    r = asyncio.run(a.edit_document(5, 1, "x.pdf", data, "fix spelling"))
    assert r.file and r.file[0].endswith("edited.pdf")
    assert "Hello world" in pymupdf.open(stream=r.file[1])[0].get_text()
