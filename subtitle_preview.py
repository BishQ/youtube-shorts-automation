"""
Generate a visual preview of the new CapCut-style subtitle design.
Saves subtitle_preview.png to the project root.
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1080, 1920
OUT = Path("subtitle_preview.png")

# ── Colour helpers ────────────────────────────────────────────────────────────
def rgba(hex_abgr: str, override_a: int | None = None) -> tuple[int,int,int,int]:
    """Convert ASS &HAABBGGRR hex to RGBA tuple for PIL."""
    h = hex_abgr.lstrip("&H").zfill(8)
    a = int(h[0:2], 16)
    b = int(h[2:4], 16)
    g = int(h[4:6], 16)
    r = int(h[6:8], 16)
    alpha = 255 - (override_a if override_a is not None else a)
    return (r, g, b, alpha)

# Active box: yellow, opaque   → RGBA (255,255,0,255)
# Active text: black           → RGBA (0,0,0,255)
# Inactive box: dark, ~50% opc → RGBA (0,0,0,128)
# Inactive text: white, dim    → RGBA (255,255,255,112)  ≈ 44% opacity
ACTIVE_BOX   = (255, 220, 0, 255)      # warm yellow box
ACTIVE_TEXT  = (0, 0, 0, 255)          # black text
INACTIVE_BOX = (0, 0, 0, 130)          # semi-transparent dark box
INACTIVE_TEXT= (255, 255, 255, 110)    # dim white text
BG_DARK      = (22, 20, 30, 255)       # dark cinematic background

# ── Font loading ──────────────────────────────────────────────────────────────
FONT_SIZE      = 100
BOX_PADDING_X  = 18   # horizontal padding inside each word box
BOX_PADDING_Y  = 10   # vertical padding inside each word box
WORD_GAP       = 16   # gap between word boxes
BOX_RADIUS     = 14   # rounded corners radius

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf", "Arial Bold.ttf", "ariblk.ttf",        # Arial Black / Bold
        "C:/Windows/Fonts/ariblk.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def rounded_rect(draw: ImageDraw.ImageDraw, xy, fill, radius: int) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def draw_word(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    cx: int,      # center-x of box
    cy: int,      # center-y of row
    active: bool,
) -> int:
    """Draw one word box.  Returns the width of the box (including padding)."""
    # Measure
    bb = font.getbbox(text)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]

    bw = tw + BOX_PADDING_X * 2
    bh = th + BOX_PADDING_Y * 2

    x0 = cx - bw // 2
    y0 = cy - bh // 2
    x1 = x0 + bw
    y1 = y0 + bh

    box_fill   = ACTIVE_BOX   if active else INACTIVE_BOX
    text_fill  = ACTIVE_TEXT  if active else INACTIVE_TEXT

    # Draw box on a temporary RGBA layer so inactive box is semi-transparent
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    rounded_rect(ldraw, (x0, y0, x1, y1), fill=box_fill, radius=BOX_RADIUS)
    base.alpha_composite(layer)

    # Draw text on top
    tx = x0 + BOX_PADDING_X - bb[0]
    ty = y0 + BOX_PADDING_Y - bb[1]
    draw.text((tx, ty), text, font=font, fill=text_fill)

    return bw


def measure_row_width(font, words: list[str]) -> int:
    total = 0
    for i, w in enumerate(words):
        bb = font.getbbox(w)
        tw = bb[2] - bb[0]
        total += tw + BOX_PADDING_X * 2
        if i < len(words) - 1:
            total += WORD_GAP
    return total


def draw_row(base: Image.Image, draw: ImageDraw.ImageDraw, font, words: list[str],
             active_idx: int, cy: int) -> None:
    total_w = measure_row_width(font, words)
    x = (W - total_w) // 2  # left edge of first word
    for i, word in enumerate(words):
        bb = font.getbbox(word)
        tw = bb[2] - bb[0]
        bw = tw + BOX_PADDING_X * 2
        cx = x + bw // 2
        draw_word(base, draw, font, word, cx, cy, active=(i == active_idx))
        x += bw + WORD_GAP


# ── Build preview ─────────────────────────────────────────────────────────────
def build_preview() -> None:
    font_big  = _load_font(FONT_SIZE)
    font_med  = _load_font(78)
    font_sm   = _load_font(52)

    # Background: dark gradient simulation
    img = Image.new("RGBA", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Fake background image texture (dark moody)
    for y in range(H):
        t = y / H
        gray = int(22 + t * 30)
        layer = Image.new("RGBA", (W, 1), (gray, gray - 5, gray + 10, 255))
        img.paste(layer, (0, y))

    draw = ImageDraw.Draw(img)

    # ── Section 1: three word-group at the bottom (video subtitle zone) ───────
    subtitle_y = int(H * 0.82)
    row_gap = FONT_SIZE + BOX_PADDING_Y * 2 + 24

    # Row A — active word is middle
    draw_row(img, draw, font_big, ["HE", "BECAME", "A"], active_idx=1, cy=subtitle_y - row_gap)

    # Row B — active word is first (normal case)
    draw_row(img, draw, font_big, ["LEGEND", "BY", "NEVER"], active_idx=0, cy=subtitle_y)

    # Row C — emphasis word example (different emotion color — orange)
    for i, (word, active, colour) in enumerate([
        ("STOPPING",  True,  (255, 140, 0, 255)),
        ("TO",        False, None),
        ("LOOK",      False, None),
    ]):
        words = ["STOPPING", "TO", "LOOK"]
        break
    # Draw emphasis row manually
    emph_words = ["STOPPING", "TO", "LOOK"]
    total_w = measure_row_width(font_big, emph_words)
    x = (W - total_w) // 2
    for i, word in enumerate(emph_words):
        bb = font_big.getbbox(word)
        tw = bb[2] - bb[0]
        bw = tw + BOX_PADDING_X * 2
        cx = x + bw // 2
        if i == 0:  # emphasis (orange box)
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ldraw = ImageDraw.Draw(layer)
            bh = (bb[3] - bb[1]) + BOX_PADDING_Y * 2
            x0, y0 = cx - bw//2, subtitle_y + row_gap - bh//2
            rounded_rect(ldraw, (x0, y0, x0+bw, y0+bh), fill=(220, 60, 0, 255), radius=BOX_RADIUS)
            img.alpha_composite(layer)
            draw2 = ImageDraw.Draw(img)
            draw2.text((x0 + BOX_PADDING_X - bb[0], y0 + BOX_PADDING_Y - bb[1]),
                       word, font=font_big, fill=(255, 255, 255, 255))
        else:
            draw_word(img, draw, font_big, word, cx, subtitle_y + row_gap, active=False)
        x += bw + WORD_GAP

    # ── Section 2: label cards ────────────────────────────────────────────────
    label_x = 60
    card_y = int(H * 0.10)
    card_h = 90
    card_pad = 20

    def label_card(text: str, y: int, box_col: tuple, text_col: tuple) -> None:
        bb = font_sm.getbbox(text)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        x0, y0 = label_x, y
        x1, y1 = x0 + tw + card_pad*2, y0 + th + card_pad
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        rounded_rect(ldraw, (x0, y0, x1, y1), fill=box_col, radius=10)
        img.alpha_composite(layer)
        draw3 = ImageDraw.Draw(img)
        draw3.text((x0 + card_pad - bb[0], y0 + card_pad//2 - bb[1]),
                   text, font=font_sm, fill=text_col)
        return y1 + 16

    cy = card_y
    cy = label_card("✦ ACTIVE WORD  →  yellow box + black text", cy,
                    (255, 220, 0, 230), (0, 0, 0, 255)) + 4
    cy = label_card("✦ INACTIVE WORDS  →  dark box + dim white", cy,
                    (40, 40, 40, 200), (255, 255, 255, 200)) + 4
    cy = label_card("✦ EMPHASIS WORD  →  orange box + white text", cy,
                    (220, 60, 0, 230), (255, 255, 255, 255)) + 4
    cy = label_card("✦ Font: Arial Black  100 px  Bold", cy,
                    (60, 60, 100, 200), (200, 200, 255, 255)) + 4
    cy = label_card("✦ Pop: 150% → 100% scale in 90 ms", cy,
                    (60, 60, 100, 200), (200, 200, 255, 255)) + 4

    # Title
    title = "SUBTITLE DESIGN — CapCut Fill Style"
    tbb = font_med.getbbox(title)
    draw.text(
        ((W - (tbb[2]-tbb[0])) // 2, cy + 20),
        title, font=font_med, fill=(255, 255, 255, 220)
    )

    # Save
    final = img.convert("RGB")
    final.save(str(OUT))
    print(f"Preview saved: {OUT.resolve()}")


if __name__ == "__main__":
    build_preview()
