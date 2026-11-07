"""Photo edits done with Pillow (no AI model): the language model only turns the request into a short list of steps."""
from __future__ import annotations

import io
import json
import os
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

EDIT_WORDS = re.compile(
    r"(?i)\b(crop|rotate|flip|mirror|resize|black ?(and|&|n) ?white|b ?& ?w|bw|grayscale|greyscale|sepia|vintage|filter|brighten|"
    r"brightness|darken|contrast|saturat|blur|sharpen|invert|border|frame|enhance|add text|write on|text lagao|caption|"
    r"edit|squar|portrait|wallpaper|thumbnail|compress|chhota|bada kar|ghuma|palat|kaala|safed|dhundla|chamak)"
)

SYSTEM = """You turn a photo-edit request into JSON for a Pillow pipeline. Reply with ONLY a JSON object, no prose:
{"ops":[...], "cannot":"" }
Allowed ops (use only these):
{"op":"rotate","deg":90}  (positive = counter-clockwise; any angle)
{"op":"flip","dir":"h"|"v"}
{"op":"crop","ratio":"1:1"|"4:5"|"16:9"|"9:16"|"3:4"|"4:3"}  (centered)
{"op":"resize","max":1080}
{"op":"gray"}  {"op":"sepia"}  {"op":"vintage"}  {"op":"invert"}  {"op":"auto"}  (auto = auto contrast/enhance)
{"op":"brightness","f":1.3}  {"op":"contrast","f":1.3}  {"op":"saturation","f":1.4}  (1.0 = no change)
{"op":"blur","r":4}  {"op":"sharpen"}
{"op":"border","px":30,"color":"white"}
{"op":"text","text":"exact words","pos":"top"|"center"|"bottom","color":"white"}
If the request needs things this list cannot do (remove background or objects, change faces, change sky or clothes, AI art style), put ops [] and a one-line reason in "cannot". Never invent text the user did not give."""

_COLORS = {"white", "black", "red", "yellow", "blue", "green", "pink", "orange", "purple", "gold", "grey", "gray"}


def parse_ops(raw: str) -> tuple[list[dict], str]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return [], "I could not understand the edit"
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return [], "I could not understand the edit"
    ops = [o for o in (data.get("ops") or []) if isinstance(o, dict) and o.get("op")][:8]
    return ops, str(data.get("cannot") or "")[:200]


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _font(text: str, size: int):
    here = os.path.join(os.path.dirname(__file__), "fonts")
    name = "NotoSansDevanagari-Regular.ttf" if re.search(r"[\u0900-\u097F]", text) else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(here, name), size)


def apply_ops(data: bytes, ops: list[dict]) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    done = []
    for o in ops:
        k = o.get("op")
        if k == "rotate":
            img = img.rotate(_num(o.get("deg"), -360, 360, 90), expand=True, fillcolor=(255, 255, 255))
        elif k == "flip":
            img = ImageOps.mirror(img) if o.get("dir") == "h" else ImageOps.flip(img)
        elif k == "crop":
            m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", str(o.get("ratio", "")))
            if m and int(m.group(2)):
                rw, rh = int(m.group(1)), int(m.group(2))
                w, h = img.size
                tw, th = (w, int(w * rh / rw)) if w * rh / rw <= h else (int(h * rw / rh), h)
                l, t = (w - tw) // 2, (h - th) // 2
                img = img.crop((l, t, l + tw, t + th))
        elif k == "resize":
            mx = int(_num(o.get("max"), 64, 4096, 1080))
            img.thumbnail((mx, mx))
        elif k == "gray":
            img = ImageOps.grayscale(img).convert("RGB")
        elif k == "sepia":
            g = ImageOps.grayscale(img)
            img = ImageOps.colorize(g, (30, 18, 8), (255, 236, 200))
        elif k == "vintage":
            g = ImageEnhance.Color(img).enhance(0.6)
            g = ImageEnhance.Contrast(g).enhance(0.9)
            r, gg, b = g.split()
            r = r.point(lambda x: min(255, int(x * 1.08 + 8)))
            b = b.point(lambda x: int(x * 0.88))
            img = Image.merge("RGB", (r, gg, b))
        elif k == "invert":
            img = ImageOps.invert(img)
        elif k == "auto":
            img = ImageEnhance.Color(ImageOps.autocontrast(img, cutoff=1)).enhance(1.1)
        elif k == "brightness":
            img = ImageEnhance.Brightness(img).enhance(_num(o.get("f"), 0.3, 2.5, 1.2))
        elif k == "contrast":
            img = ImageEnhance.Contrast(img).enhance(_num(o.get("f"), 0.3, 2.5, 1.2))
        elif k == "saturation":
            img = ImageEnhance.Color(img).enhance(_num(o.get("f"), 0.0, 3.0, 1.3))
        elif k == "blur":
            img = img.filter(ImageFilter.GaussianBlur(_num(o.get("r"), 0.5, 30, 4)))
        elif k == "sharpen":
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2))
        elif k == "border":
            col = str(o.get("color", "white")).lower()
            img = ImageOps.expand(img, border=int(_num(o.get("px"), 2, 200, 30)), fill=col if col in _COLORS else "white")
        elif k == "text":
            text = " ".join(str(o.get("text", "")).split())[:80]
            if not text:
                continue
            col = str(o.get("color", "white")).lower()
            col = col if col in _COLORS else "white"
            w, h = img.size
            size = max(18, int(h * 0.08))
            font = _font(text, size)
            while size > 14 and font.getlength(text) > w * 0.9:
                size -= 3
                font = _font(text, size)
            y = {"top": h * 0.08, "center": h / 2}.get(o.get("pos"), h * 0.9)
            d = ImageDraw.Draw(img)
            d.text((w / 2, y), text, font=font, fill=col, anchor="mm", stroke_width=max(2, size // 12),
                   stroke_fill="black" if col != "black" else "white")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


OCR_PROMPT = ("Read ALL the text in this image exactly as written, keeping its language and script (Hindi/Devanagari, English, Hinglish, numbers). "
              "Keep line breaks and table rows (use ' | ' between columns). Do not translate or explain. If a word is unreadable write [?]. "
              "If there is no text, reply exactly: NO TEXT.")
_OCR_WORDS = re.compile(r"(?i)\b(ocr|text (nikal|padh|read|extract|copy)|read (the |this )?text|extract|likha (hai|hua)|padh|nikalo|transcribe|scan|translate|anuvad|ट्रांसलेट|अनुवाद|पढ़)")


def photo_question(caption: str) -> str:
    """What to ask the vision model about a photo."""
    c = (caption or "").strip()
    if not c:
        return ("Ye photo kya hai? Short me batao. Agar photo me kuch text likha hai (kisi bhi bhasha me) to wo text bhi exactly likh do.")
    if _OCR_WORDS.search(c):
        return OCR_PROMPT + "\nThen do what the user asked: " + c
    return c
