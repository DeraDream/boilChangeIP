#!/usr/bin/env python3
import re
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FG = {
    30: (0, 0, 0),
    31: (187, 0, 0),
    32: (0, 187, 0),
    33: (170, 153, 0),
    34: (36, 114, 200),
    35: (188, 63, 188),
    36: (0, 187, 187),
    37: (187, 187, 187),
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
    41: (187, 0, 0),
    42: (0, 187, 0),
    43: (170, 153, 0),
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
FONT_SIZE = 18
LINE_GAP = 1
PADDING_X = 10
PADDING_Y = 8
DEFAULT_FG = (187, 187, 187)
DEFAULT_BG = (0, 0, 0)


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("F", "W"):
        return 2
    return 1


def find_font(size: int, candidates: list[str]) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def load_fonts(size: int):
    mono_cjk_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
    ]
    for candidate in mono_cjk_candidates:
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, size=size)
            return font, font

    ascii_font = find_font(
        size,
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        ],
    )
    cjk_font = find_font(
        size,
        [
            "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ],
    )
    return ascii_font, cjk_font


def choose_font(ch: str, ascii_font, cjk_font):
    if char_width(ch) == 2:
        return cjk_font
    return ascii_font


def glyph_bbox(font, text: str) -> tuple[int, int, int, int]:
    try:
        return font.getbbox(text)
    except Exception:
        mask = font.getmask(text)
        return 0, 0, mask.size[0], mask.size[1]


def text_pixel_width(text: str, font) -> int:
    bbox = glyph_bbox(font, text)
    return max(1, bbox[2] - bbox[0])


def parse_sgr(params: str, fg, bg, bold, italic, underline):
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
            fg, bg, bold, italic, underline = DEFAULT_FG, DEFAULT_BG, False, False, False
        elif value == 1:
            bold = True
        elif value == 3:
            italic = True
        elif value == 4:
            underline = True
        elif value == 22:
            bold = False
        elif value == 23:
            italic = False
        elif value == 24:
            underline = False
        elif value == 39:
            fg = DEFAULT_FG
        elif value == 49:
            bg = DEFAULT_BG
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
    return fg, bg, bold, italic, underline


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
    fg = DEFAULT_FG
    bg = DEFAULT_BG
    bold = False
    italic = False
    underline = False
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            match = ANSI_RE.match(text, i)
            if match:
                params, command = match.groups()
                if command == "m":
                    fg, bg, bold, italic, underline = parse_sgr(params, fg, bg, bold, italic, underline)
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
                lines[-1].append((x, ch, width, fg, bg, bold, italic, underline))
                x += width
        i += 1
    return lines


def render(input_path: Path, output_path: Path):
    text = input_path.read_text(encoding="utf-8", errors="replace")
    lines = parse_ansi(text)
    ascii_font, cjk_font = load_fonts(FONT_SIZE)

    ascii_bbox = glyph_bbox(ascii_font, "M")
    cjk_bbox = glyph_bbox(cjk_font, "测")
    cell_w = max(
        7,
        text_pixel_width("M", ascii_font),
        (text_pixel_width("测", cjk_font) + 1) // 2,
    )
    cell_h = max(
        16,
        ascii_bbox[3] - ascii_bbox[1],
        cjk_bbox[3] - cjk_bbox[1],
    ) + LINE_GAP
    max_cols = 1
    for line in lines:
        for x, _ch, width, _fg, _bg, _bold, _italic, _underline in line:
            max_cols = max(max_cols, x + width)

    image = Image.new(
        "RGB",
        (PADDING_X * 2 + max_cols * cell_w, PADDING_Y * 2 + len(lines) * cell_h),
        DEFAULT_BG,
    )
    draw = ImageDraw.Draw(image)

    for row, line in enumerate(lines):
        y = PADDING_Y + row * cell_h
        for x, ch, width, fg, bg, bold, italic, underline in line:
            px = PADDING_X + x * cell_w
            width_px = max(1, width) * cell_w
            font = choose_font(ch, ascii_font, cjk_font)
            bbox = glyph_bbox(font, ch)
            text_x = px - min(0, bbox[0])
            text_y = y - min(0, bbox[1])
            draw.rectangle([px, y, px + width_px, y + cell_h], fill=bg)
            draw.text((text_x, text_y), ch, font=font, fill=fg)
            if bold:
                draw.text((text_x + 1, text_y), ch, font=font, fill=fg)
            if underline:
                underline_y = y + cell_h - 2
                draw.line((px, underline_y, px + max(1, width_px - 1), underline_y), fill=fg, width=1)

    image.save(output_path, "PNG")


def main():
    if len(sys.argv) != 3:
        print("Usage: render_ansi_png.py input.ansi output.png", file=sys.stderr)
        return 2
    render(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
