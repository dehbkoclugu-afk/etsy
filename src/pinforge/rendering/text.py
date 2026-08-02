from __future__ import annotations

from collections.abc import Callable

from PIL import ImageDraw, ImageFont


class TextFitError(ValueError):
    pass


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.replace("\n", " \n ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        if word == "\n":
            if current:
                lines.append(current)
                current = ""
            continue
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if draw.textlength(word, font=font) <= max_width:
            current = word
            continue
        chunk = ""
        for char in word:
            candidate = chunk + char
            if chunk and draw.textlength(candidate, font=font) > max_width:
                lines.append(chunk)
                chunk = char
            else:
                chunk = candidate
        current = chunk
    if current:
        lines.append(current)
    return lines or [""]


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_factory: Callable[[int], ImageFont.FreeTypeFont],
    box: tuple[int, int, int, int],
    *,
    max_size: int,
    min_size: int,
    max_lines: int,
    spacing_ratio: float = 0.2,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    width = box[2] - box[0]
    height = box[3] - box[1]
    clean = " ".join(text.split())
    if not clean:
        raise TextFitError("Boş metin çizilemez")

    for size in range(max_size, min_size - 1, -2):
        font = font_factory(size)
        lines = wrap_text(draw, clean, font, width)
        bbox = font.getbbox("Ag")
        line_height = round(bbox[3] - bbox[1]) + max(4, round(size * spacing_ratio))
        if len(lines) <= max_lines and len(lines) * line_height <= height:
            return font, lines, line_height
    raise TextFitError(
        f"Metin {width} x {height} kutusuna en az {min_size}px ile sığmıyor: {clean[:80]}"
    )


def draw_text_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_factory: Callable[[int], ImageFont.FreeTypeFont],
    box: tuple[int, int, int, int],
    *,
    fill: str,
    max_size: int,
    min_size: int,
    max_lines: int,
    align: str = "left",
    valign: str = "top",
) -> None:
    font, lines, line_height = fit_text(
        draw,
        text,
        font_factory,
        box,
        max_size=max_size,
        min_size=min_size,
        max_lines=max_lines,
    )
    total_height = len(lines) * line_height
    y = box[1]
    if valign == "center":
        y += (box[3] - box[1] - total_height) // 2
    elif valign == "bottom":
        y = box[3] - total_height

    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = box[0]
        if align == "center":
            x += round((box[2] - box[0] - line_width) / 2)
        elif align == "right":
            x = round(box[2] - line_width)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
