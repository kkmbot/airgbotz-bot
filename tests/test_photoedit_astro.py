import io
from PIL import Image
from ai_bot.photoedit import apply_ops, parse_ops, EDIT_WORDS
from ai_bot.astro import compute


def _img():
    b = io.BytesIO(); Image.new("RGB", (400, 300), (200, 80, 40)).save(b, "PNG"); return b.getvalue()


def test_ops_chain():
    ops = [{"op": "crop", "ratio": "1:1"}, {"op": "rotate", "deg": 90}, {"op": "gray"}, {"op": "vintage"}, {"op": "sepia"},
           {"op": "brightness", "f": 1.4}, {"op": "blur", "r": 3}, {"op": "sharpen"}, {"op": "border", "px": 10},
           {"op": "text", "text": "धुन UK", "pos": "bottom"}, {"op": "resize", "max": 200}, {"op": "bogus"}]
    out = Image.open(io.BytesIO(apply_ops(_img(), ops)))
    assert out.format == "JPEG" and max(out.size) <= 220


def test_parse_ops():
    ops, cannot = parse_ops('sure {"ops":[{"op":"gray"}],"cannot":""} ok')
    assert ops == [{"op": "gray"}] and cannot == ""
    assert parse_ops("nonsense") [0] == []


def test_edit_words():
    assert EDIT_WORDS.search("make it black and white") and not EDIT_WORDS.search("who is this person")


def test_astro_sun_sign():
    out = compute("1995-05-17", "15:30", {"name": "Delhi", "lat": 28.65, "lon": 77.23, "tz": "Asia/Kolkata"})
    assert "Sun: Vrishabha" in out and "Lagna" in out
