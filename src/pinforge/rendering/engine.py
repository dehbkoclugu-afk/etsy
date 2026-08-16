from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

from pinforge.domain.models import BrandKit, PinCopy, SourceProduct
from pinforge.rendering.base import PIN_SIZE, RenderContext
from pinforge.rendering.image_ops import load_image
from pinforge.rendering.templates import TEMPLATES
from pinforge.rendering.text import TextFitError


class RenderError(RuntimeError):
    pass


class RenderEngine:
    def __init__(self) -> None:
        self.templates = {template.id: template for template in TEMPLATES}

    def render(
        self,
        template_id: str,
        product: SourceProduct,
        copy: PinCopy,
        brand: BrandKit | None = None,
    ) -> Image.Image:
        try:
            template = self.templates[template_id]
        except KeyError as exc:
            raise RenderError(f"Bilinmeyen şablon: {template_id}") from exc

        loaded = tuple(load_image(path) for path in product.image_paths)
        if len(loaded) < template.required_images:
            raise RenderError(
                f"{template.display_name} en az {template.required_images} görsel istiyor; "
                f"üründe {len(loaded)} var"
            )
        try:
            result = template.render(
                RenderContext(product, brand or BrandKit(), copy, loaded)
            )
        except (OSError, ValueError, TextFitError) as exc:
            raise RenderError(f"{template.display_name} üretilemedi: {exc}") from exc
        finally:
            for image in loaded:
                image.close()

        if result.size != PIN_SIZE:
            result.close()
            raise RenderError(f"Şablon yanlış boyut üretti: {result.size}")
        return result

    def render_to(
        self,
        target: str | Path,
        template_id: str,
        product: SourceProduct,
        copy: PinCopy,
        brand: BrandKit | None = None,
        *,
        overwrite: bool = False,
    ) -> Path:
        path = Path(target)
        if path.exists() and not overwrite:
            raise FileExistsError(f"Dosya zaten var: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        image = self.render(template_id, product, copy, brand)
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            image.close()
            raise RenderError("Çıktı uzantısı .png, .jpg veya .jpeg olmalı")
        try:
            with NamedTemporaryFile(
                dir=path.parent, suffix=suffix, delete=False
            ) as temp:
                temp_path = Path(temp.name)
            save_args = (
                {"quality": 88, "optimize": True}
                if suffix in {".jpg", ".jpeg"}
                else {"optimize": True}
            )
            image.save(temp_path, **save_args)
            os.replace(temp_path, path)
        finally:
            image.close()
            if "temp_path" in locals() and temp_path.exists():
                temp_path.unlink()
        return path
