from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def load_image(path: str | Path) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(
        image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )


def rounded(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    fitted = cover(image, size).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius, fill=255
    )
    fitted.putalpha(mask)
    return fitted


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"Renk #RRGGBB biçiminde olmalı: {hex_color}")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
