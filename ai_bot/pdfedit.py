"""Edit a PDF in place: only the text blocks that change are replaced; images, colours and the rest of the layout stay.
Uses PyMuPDF (AGPL-3.0, like this being a public repo). Scanned PDFs (no text layer) are not supported."""
from __future__ import annotations

import os
import re

import pymupdf

HERE = os.path.dirname(__file__)
LATIN = os.path.join(HERE, "fonts", "DejaVuSans.ttf")
DEVA = os.path.join(HERE, "fonts", "NotoSansDevanagari-Regular.ttf")
MAX_BLOCKS = 400


class PdfEditError(Exception):
    pass


def _blocks(doc):
    out = []
    for pno, page in enumerate(doc):
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            lines = []
            size, color = 11.0, 0
            for ln in b["lines"]:
                txt = "".join(s["text"] for s in ln["spans"]).strip()
                if txt:
                    lines.append(txt)
                if ln["spans"]:
                    size, color = ln["spans"][0]["size"], ln["spans"][0]["color"]
            text = "\n".join(lines).strip()
            if text:
                out.append({"page": pno, "bbox": tuple(b["bbox"]), "text": text, "size": size, "color": color})
    return out


def list_blocks(data: bytes) -> list[dict]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise PdfEditError("could not open the PDF") from e
    if doc.needs_pass:
        raise PdfEditError("the PDF is password protected")
    blocks = _blocks(doc)[:MAX_BLOCKS]
    doc.close()
    if not blocks:
        raise PdfEditError("no text layer (a scanned PDF)")
    return [{"id": i, **b} for i, b in enumerate(blocks)]


def apply_edits(data: bytes, edits: dict[int, str]) -> bytes:
    """edits: block id (from list_blocks) -> new text. Untouched blocks are left exactly as they were."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    blocks = list_blocks(data)
    by_page: dict[int, list] = {}
    for b in blocks:
        if b["id"] in edits and edits[b["id"]].strip() != b["text"].strip():
            by_page.setdefault(b["page"], []).append(b)
    for pno, items in by_page.items():
        page = doc[pno]
        for b in items:
            page.add_redact_annot(pymupdf.Rect(b["bbox"]), fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        arch = pymupdf.Archive(HERE + "/fonts")
        css = ('@font-face{font-family:lat;src:url(DejaVuSans.ttf)}'
               '@font-face{font-family:dev;src:url(NotoSansDevanagari-Regular.ttf)}')
        for b in items:
            text = edits[b["id"]].strip()
            c = b["color"]
            hexcol = "#%02x%02x%02x" % (c >> 16 & 255, c >> 8 & 255, c & 255)
            rect = pymupdf.Rect(b["bbox"])
            size = b["size"]
            safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
            for _ in range(14):
                html = f'<div style="font-family:lat,dev;font-size:{size:.1f}pt;color:{hexcol};line-height:1.15">{safe}</div>'
                spare, _scale = page.insert_htmlbox(rect, html, css=css, archive=arch, scale_low=1)
                if spare >= 0:
                    break
                size *= 0.92
            else:
                big = pymupdf.Rect(rect.x0, rect.y0, rect.x1, min(page.rect.y1 - 20, rect.y1 + rect.height))
                page.insert_htmlbox(big, html, css=css, archive=arch)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
