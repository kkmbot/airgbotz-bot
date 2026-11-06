import io
from PIL import Image
from ai_bot.images import add_caption

def test_caption_keeps_image_and_returns_jpeg():
    buf = io.BytesIO(); Image.new("RGB", (512, 512), (30, 90, 160)).save(buf, "PNG")
    for txt in ("UK MASTIZADE", "धुन म्यूज़िक", "x" * 200):
        out = add_caption(buf.getvalue(), txt)
        im = Image.open(io.BytesIO(out))
        assert im.format == "JPEG" and im.size == (512, 512)
