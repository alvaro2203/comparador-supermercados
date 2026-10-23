from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, override

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from supermarket_scraper.domain.entities import (
    Category,
    PackagingInfo,
    Product,
    ProductDetails,
    ProductListing,
    Supermarket,
)
from supermarket_scraper.domain.exceptions import SourceSchemaError
from supermarket_scraper.domain.value_objects import (
    MeasureUnit,
    Price,
    SellingMethod,
    SupermarketCode,
)
from supermarket_scraper.infrastructure.http.client import AsyncJsonClient
from supermarket_scraper.infrastructure.scrapers.base import (
    BaseSupermarketScraper,
    CategorizedItem,
    CategoryIndex,
    CategoryPage,
)
from supermarket_scraper.infrastructure.scrapers.mercadona.schemas import (
    CategoryIndexResponse,
    CategoryPageResponse,
    ListedProduct,
    ProductDetailResponse,
    Section,
)
from supermarket_scraper.infrastructure.scrapers.registry import ScraperContext, register_scraper

if TYPE_CHECKING:
    from collections.abc import Iterator

MERCADONA_CODE: Final = SupermarketCode("mercadona")
_TAGS: Final = re.compile(r"<[^>]+>")
_EAN: Final = re.compile(r"^\d{8,14}$")
_SELLING_METHODS: Final[dict[int, SellingMethod]] = {
    0: SellingMethod.UNIT,
    1: SellingMethod.WEIGHT,
    2: SellingMethod.BUNCH,
}


class MercadonaSettings(BaseSettings):
    """Mercadona-specific configuration, read from ``MERCADONA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="MERCADONA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    base_url: str = "https://tienda.mercadona.es"
    warehouse: str = "mad1"
    language: str = "es"


@dataclass(frozen=True, slots=True)
class MercadonaPageRef:
    """Second-level category whose page must be crawled."""

    category_id: int
    name: str


@register_scraper(str(MERCADONA_CODE))
class MercadonaScraper(
    BaseSupermarketScraper[MercadonaPageRef, ListedProduct, ProductDetailResponse]
):
    """Scraper for the public JSON API behind ``tienda.mercadona.es``.

    Prices depend on the logistics warehouse (``wh``), which maps to a region
    (e.g. ``mad1`` Madrid, ``vlc1`` Valencia, ``bcn1`` Barcelona).
    """

    def __init__(self, http: AsyncJsonClient, settings: MercadonaSettings) -> None:
        """Bind the HTTP client and the regional configuration."""
        super().__init__(http)
        self._settings = settings
        self._supermarket = Supermarket(
            code=MERCADONA_CODE, name="Mercadona", website=settings.base_url
        )

    @classmethod
    def from_context(cls, context: ScraperContext) -> Self:
        """Build the scraper from shared infrastructure and ``MERCADONA_*`` settings."""
        settings = MercadonaSettings()
        http = AsyncJsonClient(
            settings.base_url,
            context.http,
            headers={"Referer": f"{settings.base_url}/"},
            transport=context.transport,
        )
        return cls(http, settings)

    @property
    @override
    def supermarket(self) -> Supermarket:
        """Return the Mercadona supermarket entity."""
        return self._supermarket

    @override
    async def fetch_index(self) -> object:
        """Download the full category tree."""
        return await self._http.get_json("/api/categories/", self._params())

    @override
    def parse_index(self, payload: object) -> CategoryIndex[MercadonaPageRef]:
        """Build the two top levels of the tree and the list of pages to crawl."""
        response = _validate(CategoryIndexResponse, payload, "category index")
        categories: list[Category] = []
        pages: list[MercadonaPageRef] = []
        for top in response.results:
            if not top.published:
                continue
            categories.append(
                Category(
                    supermarket=MERCADONA_CODE,
                    external_id=str(top.id),
                    name=top.name.strip(),
                    level=0,
                    position=top.order,
                )
            )
            for child in top.categories:
                if not child.published:
                    continue
                categories.append(
                    Category(
                        supermarket=MERCADONA_CODE,
                        external_id=str(child.id),
                        name=child.name.strip(),
                        level=1,
                        position=child.order,
                        parent_external_id=str(top.id),
                    )
                )
                pages.append(MercadonaPageRef(category_id=child.id, name=child.name))
        return CategoryIndex(categories=tuple(categories), pages=tuple(pages))

    @override
    async def fetch_page(self, ref: MercadonaPageRef) -> object:
        """Download a second-level category page with all of its products."""
        return await self._http.get_json(f"/api/categories/{ref.category_id}/", self._params())

    @override
    def parse_page(self, ref: MercadonaPageRef, payload: object) -> CategoryPage[ListedProduct]:
        """Flatten the nested sections of a page into categories and categorized products."""
        response = _validate(CategoryPageResponse, payload, f"category {ref.category_id}")
        categories: list[Category] = []
        products: list[CategorizedItem[ListedProduct]] = []
        for section, parent_id, level in _walk_sections(response.categories, str(response.id), 2):
            categories.append(
                Category(
                    supermarket=MERCADONA_CODE,
                    external_id=str(section.id),
                    name=section.name.strip(),
                    level=level,
                    position=section.order,
                    parent_external_id=parent_id,
                )
            )
            products.extend(
                CategorizedItem(category_external_id=str(section.id), item=product)
                for product in section.products
            )
        return CategoryPage(categories=tuple(categories), products=tuple(products))

    @override
    def normalize_product(self, raw: ListedProduct, category_external_id: str) -> ProductListing:
        """Map a Mercadona product and its price instructions to domain objects."""
        pricing = raw.price_instructions
        product = Product(
            supermarket=MERCADONA_CODE,
            external_id=raw.id,
            name=raw.display_name.strip(),
            slug=raw.slug,
            packaging=PackagingInfo(
                packaging=raw.packaging,
                unit_size=pricing.unit_size,
                size_format=pricing.size_format,
                is_pack=pricing.is_pack,
                pack_size=pricing.pack_size,
                total_units=pricing.total_units,
                selling_method=_SELLING_METHODS.get(
                    pricing.selling_method if pricing.selling_method is not None else -1,
                    SellingMethod.UNKNOWN,
                ),
                is_approximate_size=pricing.approx_size,
            ),
            category_external_ids=(category_external_id,),
            thumbnail_url=raw.thumbnail,
            share_url=raw.share_url,
            is_available=raw.published and raw.unavailable_from is None,
            is_new_arrival=raw.is_new_arrival,
        )
        is_promotion = pricing.price_decreased or (
            pricing.previous_unit_price is not None
            and pricing.previous_unit_price > pricing.unit_price
        )
        reference_quantity, reference_unit = MeasureUnit.parse_quantity(pricing.reference_format)
        price = Price(
            amount=pricing.unit_price,
            reference_amount=(
                pricing.reference_price / reference_quantity
                if pricing.reference_price is not None
                else None
            ),
            reference_unit=reference_unit,
            previous_amount=pricing.previous_unit_price if is_promotion else None,
            bulk_amount=pricing.bulk_price,
            vat_rate=pricing.iva,
            is_promotion=is_promotion,
        )
        return ProductListing(product=product, price=price)

    @override
    def describe_page(self, ref: MercadonaPageRef) -> str:
        """Describe a page as ``category <id> (<name>)``."""
        return f"category {ref.category_id} ({ref.name})"

    @override
    async def fetch_detail(self, external_id: str) -> object:
        """Download the detail document of a product."""
        return await self._http.get_json(f"/api/products/{external_id}/", self._params())

    @override
    def parse_detail(self, payload: object) -> ProductDetailResponse:
        """Validate a product detail payload."""
        return _validate(ProductDetailResponse, payload, "product detail")

    @override
    def normalize_detail(self, raw: ProductDetailResponse) -> ProductDetails:
        """Map the detail document, cleaning HTML and validating the EAN."""
        details = raw.details
        nutrition = raw.nutrition_information
        ean = raw.ean.strip() if raw.ean else None
        photo = raw.photos[0] if raw.photos else None
        return ProductDetails(
            supermarket=MERCADONA_CODE,
            external_id=raw.id,
            ean=ean if ean and _EAN.fullmatch(ean) else None,
            brand=(details.brand if details else None) or raw.brand,
            origin=_clean_text((details.origin if details else None) or raw.origin),
            legal_name=_clean_text(details.legal_name if details else None),
            description=_clean_text(details.description if details else None),
            ingredients=_clean_text(nutrition.ingredients if nutrition else None),
            allergens=_clean_text(nutrition.allergens if nutrition else None),
            image_url=(photo.zoom or photo.regular) if photo else None,
        )

    def _params(self) -> dict[str, str]:
        """Return the query parameters every Mercadona endpoint expects."""
        return {"lang": self._settings.language, "wh": self._settings.warehouse}


def _validate[M: BaseModel](model: type[M], payload: object, what: str) -> M:
    """Validate a payload against a schema, raising a domain schema error on mismatch."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        msg = (
            f"Unexpected Mercadona {what} payload: {exc.error_count()} errors, "
            f"first at {location or '<root>'}: {first['msg']}"
        )
        raise SourceSchemaError(msg) from exc


def _walk_sections(
    sections: list[Section], parent_id: str, level: int
) -> Iterator[tuple[Section, str, int]]:
    """Yield every section of a page depth-first with its parent id and tree level."""
    for section in sections:
        yield section, parent_id, level
        yield from _walk_sections(section.categories, str(section.id), level + 1)


def _clean_text(value: str | None) -> str | None:
    """Strip HTML tags and entities from a rich-text field."""
    if value is None:
        return None
    text = " ".join(html.unescape(_TAGS.sub(" ", value)).split())
    return text or None
