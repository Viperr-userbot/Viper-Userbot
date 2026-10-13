import io
from PIL import Image, ImageDraw, ImageFont

PALETTE = [
    (255, 99, 132), (54, 162, 235), (255, 206, 86), (75, 192, 192),
    (153, 102, 255), (255, 159, 64), (46, 204, 113), (231, 76, 60),
    (26, 188, 156), (241, 196, 15), (155, 89, 182),
]

def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def render(grid, cands, cell=56) -> bytes:
    R, C = len(grid), len(grid[0])
    W, H = C * cell, R * cell
    base = Image.new("RGBA", (W, H), (22, 14, 44, 255))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)

    def center(p):
        return p[1] * cell + cell // 2, p[0] * cell + cell // 2

    for i, c in enumerate(cands):
        if c is None:
            continue
        col = PALETTE[i % len(PALETTE)]
        (x1, y1), (x2, y2) = center(c.start), center(c.end)
        w = cell - 16
        od.line([(x1, y1), (x2, y2)], fill=col + (140,), width=w)
        for x, y in ((x1, y1), (x2, y2)):
            od.ellipse([x - w // 2, y - w // 2, x + w // 2, y + w // 2], fill=col + (140,))

    img = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(img)
    f = _font(26)
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            d.text((c * cell + cell / 2, r * cell + cell / 2), ch, font=f,
                   fill=(255, 255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
