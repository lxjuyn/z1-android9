#!/usr/bin/env python3
"""Generate an Android 9 bootanimation.zip for the Z1 panel (240x320).

AOSP's BootAnimation preloads frames **in archive order** (it does not sort by
file name), so the previously shipped zip -- whose part0 starts at 00114.png --
plays scrambled.  It also rejects deflate-compressed frames in some builds, so
every entry is written STORED.

Layout produced:
  desc.txt    "240 320 15"           width height fps
              "c 1 0 part0"          play intro once
              "c 0 0 part1"          loop the spinner forever
  part0/*.png fade-in of the title
  part1/*.png rotating spinner (infinite loop)

The animation keeps running until SurfaceFlinger sets service.bootanim.exit=1,
which AOSP does only in bootFinished() -- after PackageManagerService has
finished its dexopt pass.  That is why the boot animation is the native Android
9 feedback for "optimizing system apps".
"""
import argparse, io, math, sys, zipfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

W, H = 240, 320
FPS = 15
TITLE = "正在优化系统"
SUBTITLE = "首次启动需要几分钟"
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def base_canvas():
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    f_title = load_font(22)
    f_sub = load_font(13)

    def centre(text, font, y, fill):
        tw = d.textbbox((0, 0), text, font=font)[2]
        d.text(((W - tw) // 2, y), text, font=font, fill=fill)

    centre(TITLE, f_title, 128, (235, 235, 235))
    centre(SUBTITLE, f_sub, 162, (140, 140, 140))
    return img, d


def draw_spinner(img, d, angle_deg, radius=18, width=4):
    cx, cy = W // 2, 205
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    # static faint ring
    d.ellipse(box, outline=(45, 45, 45), width=width)
    # bright arc
    d.arc(box, start=angle_deg, end=angle_deg + 100,
          fill=(0, 170, 220), width=width)


def make_frames(intro=8, spin=24):
    part0, part1 = [], []
    # part0: fade the title in, spinner static
    for i in range(intro):
        img, d = base_canvas()
        a = (i + 1) / intro
        draw_spinner(img, d, -90)
        # fade by blending toward black
        img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, a)
        buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
        part0.append(buf.getvalue())
    # part1: spinner rotates, full brightness
    for i in range(spin):
        img, d = base_canvas()
        draw_spinner(img, d, int(360 * i / spin) - 90)
        buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
        part1.append(buf.getvalue())
    return part0, part1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default="bootanimation.zip")
    ap.add_argument("--intro", type=int, default=8)
    ap.add_argument("--spin", type=int, default=24)
    a = ap.parse_args()

    part0, part1 = make_frames(a.intro, a.spin)

    desc = f"{W} {H} {FPS}\nc 1 0 part0\nc 0 0 part1\n"

    with zipfile.ZipFile(a.output, "w", zipfile.ZIP_STORED) as z:
        z.writestr("desc.txt", desc)
        for i, data in enumerate(part0):
            z.writestr("part0/%05d.png" % i, data)      # zero-padded => archive order == playback order
        for i, data in enumerate(part1):
            z.writestr("part1/%05d.png" % i, data)

    total = sum(len(x) for x in part0 + part1)
    print(f"wrote {a.output}: {len(part0)+len(part1)} frames, {total} bytes of PNG, "
          f"desc={W}x{H}@{FPS}fps, all STORED")


if __name__ == "__main__":
    main()
