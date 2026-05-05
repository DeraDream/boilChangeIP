#!/usr/bin/env python3
import re
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FG = {
    30: (0, 0, 0),
    31: (205, 49, 49),
    32: (13, 188, 121),
    33: (229, 229, 16),
    34: (36, 114, 200),
    35: (188, 63, 188),
    36: (17, 168, 205),
    37: (229, 229, 229),
    90: (102, 102, 102),
    91: (241, 76, 76),
    92: (35, 209, 139),
    93: (245, 245, 67),
    94: (59, 142, 234),
    95: (214, 112, 214),
    96: (41, 184, 219),
    97: (255, 255, 255),
}

BG = {
    40: (0, 0, 0),
    41: (205, 49, 49),
    42: (13, 188, 121),
    43: (229, 229, 16),
    44: (36, 114, 200),
    45: (188, 63, 188),
    46: (17, 168, 205),
    47: (229, 229, 229),
    100: (102, 102, 102),
    101: (241, 76, 76),
    102: (35, 209, 139),
    103: (245, 245, 67),
    104: (59, 142, 234),
    105: (214, 112, 214),
    106: (41, 184, 219),
    107: (255, 255, 255),
}

ANSI_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("F", "W"):
        return 2
    return 1


def find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def parse_sgr(params: str, fg, bg, bold):
    if not params:
        values = [0]
    else:
        values = []
        for part in params.split(";"):
            if part == "" or part == "?":
                continue
            try:
                values.append(int(part.lstrip("?")))
            except ValueError:
                pass
        if not values:
            values = [0]

    idx = 0
    while idx < len(values):
        value = values[idx]
        if value == 0:
            fg, bg, bold = (220, 220, 220), (0, 0, 0), False
        elif value == 1:
            bold = True
        elif value == 22:
            bold = False
        elif value == 39:
            fg = (220, 220, 220)
        elif value == 49:
            bg = (0, 0, 0)
        elif value in FG:
            fg = FG[value]
        elif value in BG:
            bg = BG[value]
        elif value == 38 and idx + 2 < len(values) and values[idx + 1] == 5:
            fg = xterm_256(values[idx + 2])
            idx += 2
        elif value == 48 and idx + 2 < len(values) and values[idx + 1] == 5:
            bg = xterm_256(values[idx + 2])
            idx += 2
        idx += 1
    return fg, bg, bold


def xterm_256(code: int):
    base = [
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ]
    if code < 16:
        return base[max(0, code)]
    if 16 <= code <= 231:
        code -= 16
        r = code // 36
        g = (code % 36) // 6
        b = code % 6
        conv = lambda n: 55 + n * 40 if n else 0
        return conv(r), conv(g), conv(b)
    gray = 8 + (code - 232) * 10
    return gray, gray, gray


def parse_ansi(text: str):
    lines = [[]]
    x = 0
    fg = (220, 220, 220)
    bg = (0, 0, 0)
    bold = False
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            match = ANSI_RE.match(text, i)
            if match:
                params, command = match.groups()
                if command == "m":
                    fg, bg, bold = parse_sgr(params, fg, bg, bold)
                i = match.end()
                continue
        ch = text[i]
        if ch == "\r":
            x = 0
        elif ch == "\n":
            lines.append([])
            x = 0
        elif ch == "\b":
            x = max(0, x - 1)
        elif ch.isprintable():
            width = char_width(ch)
            if width:
                lines[-1].append((x, ch, width, fg, bg, bold))
                x += width
        i += 1
    return lines


def render(input_path: Path, output_path: Path):
    text = input_path.read_text(encoding="utf-8", errors="replace")
    lines = parse_ansi(text)
    font = find_font(18)
    bold_font = font

    bbox = font.getbbox("M")
    cell_w = max(10, int(font.getlength("M")))
    cell_h = max(22, bbox[3] - bbox[1] + 7)
    padding = 14
    max_cols = 1
    for line in lines:
        for x, _ch, width, _fg, _bg, _bold in line:
            max_cols = max(max_cols, x + width)

    image = Image.new(
        "RGB",
        (padding * 2 + max_cols * cell_w, padding * 2 + len(lines) * cell_h),
        (0, 0, 0),
    )
    draw = ImageDraw.Draw(image)

    for row, line in enumerate(lines):
        y = padding + row * cell_h
        for x, ch, width, fg, bg, bold in line:
            px = padding + x * cell_w
            draw.rectangle(
                [px, y, px + max(1, width) * cell_w, y + cell_h],
                fill=bg,
            )
            draw.text((px, y + 2), ch, font=bold_font if bold else font, fill=fg)

    image.save(output_path, "PNG")


def main():
    if len(sys.argv) != 3:
        print("Usage: render_ansi_png.py input.ansi output.png", file=sys.stderr)
        return 2
    render(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
