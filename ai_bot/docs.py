"""Create and read documents: txt, md, docx, xlsx, pdf. Output is rebuilt from text, so editing loses fancy layout."""
from __future__ import annotations

import io
import os
import re

FORMATS = ("docx", "xlsx", "pdf", "txt", "md", "csv")
MAX_IN_BYTES = 8_000_000
MAX_TEXT = 12_000
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
)


class DocError(Exception):
    pass


def safe_name(title: str, fmt: str) -> str:
    base = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip().replace(" ", "_")[:40] or "document"
    return f"{base}.{fmt}"


def guess_format(text: str) -> str:
    t = text.lower()
    for key, fmt in (("docx", "docx"), ("word", "docx"), ("xlsx", "xlsx"), ("excel", "xlsx"), ("spreadsheet", "xlsx"),
                     ("pdf", "pdf"), ("csv", "csv"), ("markdown", "md"), (".md", "md"), ("txt", "txt"), ("text file", "txt")):
        if key in t:
            return fmt
    return "docx"


def _rows(content: str) -> list[list[str]]:
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line or set(line) <= set("|-: "):
            continue
        if "|" in line:
            rows.append([c.strip() for c in line.strip("|").split("|")])
        elif "," in line or "\t" in line:
            rows.append([c.strip() for c in re.split(r"[,\t]", line)])
        else:
            rows.append([line])
    return rows


def make_doc(fmt: str, title: str, content: str) -> tuple[str, bytes]:
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise DocError("formats: " + ", ".join(FORMATS))
    name = safe_name(title, fmt)
    content = content.strip()
    if fmt in ("txt", "md"):
        return name, content.encode("utf-8")
    if fmt == "csv":
        import csv
        buf = io.StringIO()
        csv.writer(buf).writerows(_rows(content))
        return name, buf.getvalue().encode("utf-8-sig")
    if fmt == "docx":
        from docx import Document
        d = Document()
        d.add_heading(title[:100] or "Document", level=1)
        for line in content.splitlines():
            s = line.rstrip()
            if not s:
                continue
            m = re.match(r"^(#{1,3})\s+(.*)", s)
            if m:
                d.add_heading(m.group(2), level=len(m.group(1)))
            elif re.match(r"^\s*[-*\u2022]\s+", s):
                d.add_paragraph(re.sub(r"^\s*[-*\u2022]\s+", "", s), style="List Bullet")
            elif re.match(r"^\s*\d+[.)]\s+", s):
                d.add_paragraph(re.sub(r"^\s*\d+[.)]\s+", "", s), style="List Number")
            else:
                d.add_paragraph(s.replace("**", ""))
        out = io.BytesIO()
        d.save(out)
        return name, out.getvalue()
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for r in _rows(content):
            ws.append([float(c) if re.fullmatch(r"-?\d+(\.\d+)?", c) else c for c in r])
        out = io.BytesIO()
        wb.save(out)
        return name, out.getvalue()
    # pdf
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    font = next((f for f in _FONT_CANDIDATES if os.path.exists(f)), None)
    if font:
        pdf.add_font("U", "", font)
        pdf.set_font("U", size=12)
        enc = lambda s: s  # noqa: E731
    else:
        pdf.set_font("Helvetica", size=12)
        enc = lambda s: s.encode("latin-1", "replace").decode("latin-1")  # noqa: E731
    pdf.set_font_size(16)
    pdf.multi_cell(0, 9, enc(title[:100] or "Document"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font_size(12)
    for line in content.splitlines():
        pdf.multi_cell(0, 7, enc(re.sub(r"^#{1,3}\s+", "", line).replace("**", "")) or " ", new_x="LMARGIN", new_y="NEXT")
    return name, bytes(pdf.output())


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """Return (format, text) for an uploaded file."""
    if len(data) > MAX_IN_BYTES:
        raise DocError("file too big (8 MB max)")
    ext = os.path.splitext(filename.lower())[1].lstrip(".")
    try:
        if ext == "docx":
            from docx import Document
            d = Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
            return "docx", "\n".join(parts)[:MAX_TEXT]
        if ext == "xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            lines = []
            for ws in wb.worksheets[:3]:
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None for c in row):
                        lines.append(" | ".join("" if c is None else str(c) for c in row))
                    if len(lines) > 400:
                        break
            return "xlsx", "\n".join(lines)[:MAX_TEXT]
        if ext == "pdf":
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(data))
            return "pdf", "\n".join((p.extract_text() or "") for p in rd.pages[:30])[:MAX_TEXT]
        if ext in ("txt", "md", "csv", "json", "py", "js", "html", "xml", "log", "yaml", "yml", "ini", ""):
            return (ext if ext in ("txt", "md", "csv") else "txt"), data.decode("utf-8", errors="replace")[:MAX_TEXT]
    except DocError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DocError("could not read that file") from e
    raise DocError("I can read txt, md, csv, docx, xlsx and pdf files")
