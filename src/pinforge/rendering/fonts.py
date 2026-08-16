from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from pinforge.domain.models import BrandKit


class FontError(RuntimeError):
    pass


class FontBook:
    def __init__(self, brand: BrandKit) -> None:
        self.brand = brand
        self.font_dir = Path(__file__).resolve().parents[1] / "assets" / "fonts"

    @lru_cache(maxsize=64)
    def headline(self, size: int) -> ImageFont.FreeTypeFont:
        return self._load(self.brand.headline_font, size)

    @lru_cache(maxsize=64)
    def body(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        name = self.brand.body_bold_font if bold else self.brand.body_font
        return self._load(name, size)

    def _load(self, name: str, size: int) -> ImageFont.FreeTypeFont:
        path = self.font_dir / name
        if not path.is_file():
            raise FontError(f"Paketlenmiş font bulunamadı: {path}")
        return ImageFont.truetype(str(path), size=size)
