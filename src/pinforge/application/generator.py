from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pinforge.domain.models import PinCopy, SourceProduct


DEFAULT_BULLETS = (
    "A warm first impression",
    "Clear house essentials",
    "Local recommendations",
    "Simple check-out steps",
    "A polished guest experience",
)


def build_copy(
    product: SourceProduct,
    template_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> PinCopy:
    pin_title = (title or product.title).strip()[:100]
    pin_description = (description or product.description).strip()
    if not pin_description:
        tag_text = ", ".join(product.tags[:3])
        pin_description = (
            f"Discover {product.title}. A ready-to-use digital template designed for a polished result. "
            f"{('Ideal for ' + tag_text + '. ') if tag_text else ''}Open the listing to see every included page and detail."
        )
    alt_text = f"Pinterest preview of {product.title}"[:125]
    bullets = DEFAULT_BULLETS if template_id == "list_stack" else ()
    return PinCopy(pin_title, pin_description[:500], alt_text, bullets)


def with_utm(listing_url: str, template_id: str, vertical: str) -> str:
    split = urlsplit(listing_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": "pinterest",
            "utm_medium": "social",
            "utm_campaign": vertical,
            "utm_content": template_id,
        }
    )
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
    )
