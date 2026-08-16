from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PIL import Image

from pinforge.domain.models import BrandKit, PinCopy, SourceProduct

PIN_SIZE = (1000, 1500)


@dataclass(frozen=True, slots=True)
class RenderContext:
    product: SourceProduct
    brand: BrandKit
    copy: PinCopy
    images: tuple[Image.Image, ...]


class PinTemplate(ABC):
    id: str
    display_name: str
    required_images: int

    @abstractmethod
    def render(self, context: RenderContext) -> Image.Image:
        raise NotImplementedError
