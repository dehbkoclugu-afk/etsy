from __future__ import annotations

from PIL import Image, ImageDraw

from pinforge.rendering.base import PIN_SIZE, PinTemplate, RenderContext
from pinforge.rendering.fonts import FontBook
from pinforge.rendering.image_ops import cover, rgb, rounded
from pinforge.rendering.text import draw_text_box


def _shop_footer(
    draw: ImageDraw.ImageDraw, fonts: FontBook, ctx: RenderContext, y: int, *, fill: str
) -> None:
    draw.text(
        (64, y), ctx.brand.shop_name.upper(), font=fonts.body(28, bold=True), fill=fill
    )
    marker = "DIGITAL TEMPLATE"
    width = draw.textlength(marker, font=fonts.body(22))
    draw.text((936 - width, y + 3), marker, font=fonts.body(22), fill=fill)


def _badge(
    draw: ImageDraw.ImageDraw,
    fonts: FontBook,
    text: str,
    xy: tuple[int, int],
    fill: str,
    ink: str,
) -> None:
    font = fonts.body(20, bold=True)
    width = round(draw.textlength(text, font=font)) + 42
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + 48), radius=24, fill=fill)
    draw.text((x + 21, y + 12), text, font=font, fill=ink)


class MockupHero(PinTemplate):
    id = "mockup_hero"
    display_name = "Mockup Hero"
    required_images = 1

    def render(self, context: RenderContext) -> Image.Image:
        canvas = Image.new("RGB", PIN_SIZE, rgb(context.brand.surface))
        canvas.paste(cover(context.images[0], (1000, 930)), (0, 0))
        draw = ImageDraw.Draw(canvas)
        fonts = FontBook(context.brand)
        draw.rectangle((0, 930, 1000, 1500), fill=rgb(context.brand.primary))
        if context.copy.badge_text:
            _badge(
                draw,
                fonts,
                context.copy.badge_text,
                (64, 64),
                context.brand.surface,
                context.brand.primary,
            )
        draw_text_box(
            draw,
            context.copy.title,
            fonts.headline,
            (64, 1000, 936, 1350),
            fill="#FAF8F1",
            max_size=82,
            min_size=42,
            max_lines=3,
            valign="center",
        )
        _shop_footer(draw, fonts, context, 1415, fill="#FAF8F1")
        return canvas


class ListStack(PinTemplate):
    id = "list_stack"
    display_name = "List Stack"
    required_images = 0

    def render(self, context: RenderContext) -> Image.Image:
        canvas = Image.new("RGB", PIN_SIZE, rgb(context.brand.surface))
        draw = ImageDraw.Draw(canvas)
        fonts = FontBook(context.brand)
        draw.rectangle((0, 0, 26, 1500), fill=rgb(context.brand.accent))
        draw_text_box(
            draw,
            context.copy.title,
            fonts.headline,
            (78, 72, 922, 390),
            fill=context.brand.primary,
            max_size=76,
            min_size=40,
            max_lines=3,
            valign="center",
        )
        bullets = context.copy.bullets or (
            "A warm first impression",
            "Clear house essentials",
            "Local recommendations",
            "Simple check-out steps",
            "A polished guest experience",
        )
        y = 450
        for index, bullet in enumerate(bullets[:5], start=1):
            draw.ellipse((78, y, 136, y + 58), fill=rgb(context.brand.primary))
            number = str(index)
            number_font = fonts.body(24, bold=True)
            number_width = draw.textlength(number, font=number_font)
            draw.text(
                (107 - number_width / 2, y + 14),
                number,
                font=number_font,
                fill=context.brand.surface,
            )
            draw_text_box(
                draw,
                bullet,
                lambda size: fonts.body(size, bold=True),
                (166, y - 3, 922, y + 70),
                fill=context.brand.ink,
                max_size=34,
                min_size=24,
                max_lines=2,
                valign="center",
            )
            if index < min(5, len(bullets)):
                draw.line((166, y + 92, 922, y + 92), fill="#D7D0C2", width=2)
            y += 175
        _shop_footer(draw, fonts, context, 1415, fill=context.brand.primary)
        return canvas


class SplitCompare(PinTemplate):
    id = "split_compare"
    display_name = "Split Compare"
    required_images = 2

    def render(self, context: RenderContext) -> Image.Image:
        canvas = Image.new("RGB", PIN_SIZE, rgb(context.brand.surface))
        canvas.paste(cover(context.images[0], (1000, 650)), (0, 0))
        canvas.paste(cover(context.images[1], (1000, 650)), (0, 850))
        draw = ImageDraw.Draw(canvas)
        fonts = FontBook(context.brand)
        draw.rectangle((0, 650, 1000, 850), fill=rgb(context.brand.primary))
        draw_text_box(
            draw,
            context.copy.title,
            fonts.headline,
            (54, 672, 946, 824),
            fill="#FAF8F1",
            max_size=56,
            min_size=32,
            max_lines=2,
            align="center",
            valign="center",
        )
        _badge(
            draw,
            fonts,
            "TWO STYLES",
            (64, 54),
            context.brand.surface,
            context.brand.primary,
        )
        return canvas


class TextOverlay(PinTemplate):
    id = "text_overlay"
    display_name = "Text Overlay"
    required_images = 1

    def render(self, context: RenderContext) -> Image.Image:
        canvas = cover(context.images[0], PIN_SIZE).convert("RGBA")
        overlay = Image.new("RGBA", PIN_SIZE, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        primary = rgb(context.brand.primary)
        for y in range(500, 1500):
            # Product previews often contain their own text. Reach near-opaque
            # before the headline zone so that source copy cannot compete.
            alpha = min(255, round(255 * (y - 500) / 430))
            overlay_draw.line((0, y, 1000, y), fill=(*primary, alpha))
        canvas = Image.alpha_composite(canvas, overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)
        fonts = FontBook(context.brand)
        if context.copy.badge_text:
            _badge(
                draw,
                fonts,
                context.copy.badge_text,
                (64, 64),
                context.brand.surface,
                context.brand.primary,
            )
        draw_text_box(
            draw,
            context.copy.title,
            fonts.headline,
            (64, 955, 936, 1325),
            fill="#FAF8F1",
            max_size=82,
            min_size=46,
            max_lines=4,
            valign="bottom",
        )
        _shop_footer(draw, fonts, context, 1415, fill="#FAF8F1")
        return canvas


class GridPreview(PinTemplate):
    id = "grid_preview"
    display_name = "Grid Preview"
    required_images = 1

    def render(self, context: RenderContext) -> Image.Image:
        canvas = Image.new("RGB", PIN_SIZE, rgb(context.brand.surface))
        draw = ImageDraw.Draw(canvas)
        fonts = FontBook(context.brand)
        draw_text_box(
            draw,
            context.copy.title,
            fonts.headline,
            (54, 42, 946, 252),
            fill=context.brand.primary,
            max_size=62,
            min_size=34,
            max_lines=2,
            align="center",
            valign="center",
        )
        images = context.images
        cells = ((54, 285), (513, 285), (54, 770), (513, 770))
        for index, position in enumerate(cells):
            source = images[index % len(images)]
            tile = rounded(source, (433, 445), 24)
            canvas.paste(tile, position, tile)
        draw.rounded_rectangle(
            (330, 1260, 670, 1340), radius=40, fill=rgb(context.brand.primary)
        )
        badge = f"SET OF {max(1, len(images))}"
        badge_font = fonts.body(28, bold=True)
        width = draw.textlength(badge, font=badge_font)
        draw.text(
            (500 - width / 2, 1283), badge, font=badge_font, fill=context.brand.surface
        )
        _shop_footer(draw, fonts, context, 1415, fill=context.brand.primary)
        return canvas


TEMPLATES: tuple[PinTemplate, ...] = (
    MockupHero(),
    ListStack(),
    SplitCompare(),
    TextOverlay(),
    GridPreview(),
)
