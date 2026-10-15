"""Generate the SciSketch app icon (PNG + multi-size ICO). Run: python make_icon.py   (needs Pillow, installed with matplotlib)"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

S = 2048
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, c1, c2, c3):
    g = Image.new("RGB", (size, size)); px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px[x, y] = lerp(c1, c2, t * 2) if t < 0.5 else lerp(c2, c3, (t - 0.5) * 2)
    return g


def mask(size, r):
    m = Image.new("L", (size, size), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255); return m


def main():
    os.makedirs(OUT, exist_ok=True)
    pad = int(S * 0.04); n = S - 2 * pad; R = int(n * 0.23)
    tile = gradient(n, (30, 78, 216), (96, 82, 238), (20, 196, 190)).convert("RGBA")
    tile.putalpha(mask(n, R))
    hl = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([-n * .2, -n * .8, n * 1.2, n * .42], fill=(255, 255, 255, 40))
    hl = hl.filter(ImageFilter.GaussianBlur(n * .03)); hl.putalpha(Image.composite(hl.split()[3], Image.new("L", (n, n), 0), mask(n, R)))
    tile = Image.alpha_composite(tile, hl)

    g = Image.new("RGBA", (n, n), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    px_, py_ = n * 0.5, n * 0.17                                    # pivot
    L = n * 0.46
    # reference trajectory (dashed arc) + swing sector
    box = [px_ - L, py_ - L, px_ + L, py_ + L]
    for a in range(30, 150, 7):
        d.arc(box, a, a + 4, fill=(255, 255, 255, 120), width=int(n * 0.016))
    ang = math.radians(118)                                         # bob position on the arc
    bx, by = px_ + L * math.cos(ang), py_ + L * math.sin(ang)
    # glow
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0)); ImageDraw.Draw(glow).ellipse([bx - n * .13, by - n * .13, bx + n * .13, by + n * .13], fill=(255, 255, 255, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(n * .05)); g = Image.alpha_composite(g, glow); d = ImageDraw.Draw(g)
    d.line([(px_, py_), (bx, by)], fill=(255, 255, 255, 255), width=int(n * 0.034))
    d.ellipse([px_ - n * .03, py_ - n * .03, px_ + n * .03, py_ + n * .03], fill=(255, 255, 255, 255))
    d.ellipse([bx - n * .085, by - n * .085, bx + n * .085, by + n * .085], fill=(255, 255, 255, 255))
    # faint "reference truth" ring on the bob
    d.ellipse([bx - n * .125, by - n * .125, bx + n * .125, by + n * .125], outline=(255, 255, 255, 170), width=int(n * 0.012))
    # energy wave along the bottom
    pts = [(n * (0.14 + 0.72 * i / 120), n * 0.80 - n * 0.055 * math.sin(i / 120 * 2 * math.pi * 2)) for i in range(121)]
    d.line(pts, fill=(253, 230, 138, 255), width=int(n * 0.03), joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - n * .015, p[1] - n * .015, p[0] + n * .015, p[1] + n * .015], fill=(253, 230, 138, 255))
    # verified badge (bottom right)
    cx, cy, r = n * 0.80, n * 0.74, n * 0.115
    sh = Image.new("RGBA", (n, n), (0, 0, 0, 0)); ImageDraw.Draw(sh).ellipse([cx - r, cy - r + n * .012, cx + r, cy + r + n * .012], fill=(0, 0, 0, 90))
    g = Image.alpha_composite(g, sh.filter(ImageFilter.GaussianBlur(n * .012))); d = ImageDraw.Draw(g)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(52, 211, 153, 255), outline=(255, 255, 255, 255), width=int(n * 0.012))
    d.line([(cx - r * .45, cy + r * .02), (cx - r * .1, cy + r * .38), (cx + r * .5, cy - r * .32)], fill=(255, 255, 255, 255), width=int(n * 0.03), joint="curve")
    for p in ((cx - r * .45, cy + r * .02), (cx + r * .5, cy - r * .32)):
        d.ellipse([p[0] - n * .015, p[1] - n * .015, p[0] + n * .015, p[1] + n * .015], fill=(255, 255, 255, 255))
    tile = Image.alpha_composite(tile, g)

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0)); canvas.paste(tile, (pad, pad), tile)
    canvas.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "scisketch.png"))
    canvas.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "scisketch.ico"),
                                                 sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    strip = Image.new("RGBA", (512 + 250, 512), (24, 28, 52, 255)); big = canvas.resize((512, 512), Image.LANCZOS); strip.paste(big, (0, 0), big)
    x = 530
    for sz in (128, 64, 32):
        im = canvas.resize((sz, sz), Image.LANCZOS); strip.paste(im, (x, 20), im); x += sz + 10
    strip.save("/tmp/ps_icon_preview.png")
    print("icon written")


if __name__ == "__main__":
    main()
