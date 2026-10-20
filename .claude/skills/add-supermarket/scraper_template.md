# Scraper package template

Replace `Dia`/`dia`/`DIA` with the chain. Paths, endpoints and field names below are placeholders:
take the real ones from the researcher's report. Keep every docstring; no inline comments.

## `schemas.py`

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DiaModel(BaseModel):
    """Base for DIA payloads: tolerant to unknown fields, immutable once parsed."""

    model_config = ConfigDict(extra="ignore", frozen=True, coerce_numbers_to_str=True)


class DiaCategoryNode(DiaModel):
    """Category as listed in the category tree."""

    id: str
    name: str
    children: list[DiaCategoryNode] = Field(default_factory=list)


class DiaCategoryTree(DiaModel):
    """Payload of the category tree endpoint."""

    categories: list[DiaCategoryNode]


class DiaProduct(DiaModel):
    """Product entry inside a category page."""

    id: str
    name: str
    price: Decimal
    price_per_unit: Decimal | None = None
    unit: str | None = None


class DiaCategoryPage(DiaModel):
    """Payload of one category listing page."""

    products: list[DiaProduct] = Field(default_factory=list)


class DiaProductDetail(DiaModel):
    """Payload of the product detail endpoint."""

    id: str
    ean: str | None = None
    brand: str | None = None
```

## `scraper.py`

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Self, override

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from supermarket_scraper.domain.entities import (
    Category,
    Product,
    ProductDetails,
    ProductListing,
    Supermarket,
)
from supermarket_scraper.domain.exceptions import SourceSchemaError
from supermarket_scraper.domain.value_objects import MeasureUnit, Price, SupermarketCode
from supermarket_scraper.infrastructure.http.client import AsyncJsonClient
from supermarket_scraper.infrastructure.scrapers.base import (
    BaseSupermarketScraper,
    CategorizedItem,
    CategoryIndex,
    CategoryPage,
)
from supermarket_scraper.infrastructure.scrapers.dia.schemas import (
    DiaCategoryPage,
    DiaCategoryTree,
    DiaProduct,
    DiaProductDetail,
)
from supermarket_scraper.infrastructure.scrapers.registry import ScraperContext, register_scraper

DIA_CODE: Final = SupermarketCode("dia")
_EAN: Final = re.compile(r"^\d{8,14}$")


class DiaSettings(BaseSettings):
    """DIA-specific configuration, read from ``DIA_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DIA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    base_url: str = "https://www.dia.es"


@dataclass(frozen=True, slots=True)
class DiaPageRef:
    """Category page that must be crawled."""

    category_id: str
    name: str


@register_scraper(str(DIA_CODE))
class DiaScraper(BaseSupermarketScraper[DiaPageRef, DiaProduct, DiaProductDetail]):
    """Scraper for the JSON API behind DIA's online shop."""

    def __init__(self, http: AsyncJsonClient, settings: DiaSettings) -> None:
        """Bind the HTTP client and the chain configuration."""
        super().__init__(http)
        self._settings = settings
        self._supermarket = Supermarket(code=DIA_CODE, name="DIA", website=settings.base_url)

    @classmethod
    def from_context(cls, context: ScraperContext) -> Self:
        """Build the scraper from shared infrastructure and ``DIA_*`` settings."""
        settings = DiaSettings()
        http = AsyncJsonClient(settings.base_url, context.http, transport=context.transport)
        return cls(http, settings)

    @property
    @override
    def supermarket(self) -> Supermarket:
        """Return the DIA supermarket entity."""
        return self._supermarket

    @override
    async def fetch_index(self) -> object:
        """Download the category tree."""
        return await self._http.get_json("/api/categories")

    @override
    def parse_index(self, payload: object) -> CategoryIndex[DiaPageRef]:
        """Build the category tree and the list of leaf pages to crawl."""
        tree = _validate(DiaCategoryTree, payload, "category tree")
        categories: list[Category] = []
        pages: list[DiaPageRef] = []
        ...
        return CategoryIndex(categories=tuple(categories), pages=tuple(pages))

    @override
    async def fetch_page(self, ref: DiaPageRef) -> object:
        """Download one category page."""
        return await self._http.get_json(f"/api/categories/{ref.category_id}/products")

    @override
    def parse_page(self, ref: DiaPageRef, payload: object) -> CategoryPage[DiaProduct]:
        """Extract the products of a category page."""
        page = _validate(DiaCategoryPage, payload, f"category {ref.category_id}")
        return CategoryPage(
            categories=(),
            products=tuple(
                CategorizedItem(category_external_id=ref.category_id, item=product)
                for product in page.products
            ),
        )

    @override
    def normalize_product(self, raw: DiaProduct, category_external_id: str) -> ProductListing:
        """Map a DIA product and its price to domain objects."""
        product = Product(
            supermarket=DIA_CODE,
            external_id=raw.id,
            name=raw.name.strip(),
            category_external_ids=(category_external_id,),
        )
        price = Price(
            amount=raw.price,
            reference_amount=raw.price_per_unit,
            reference_unit=MeasureUnit.parse(raw.unit),
        )
        return ProductListing(product=product, price=price)

    @override
    def describe_page(self, ref: DiaPageRef) -> str:
        """Describe a page as ``category <id> (<name>)``."""
        return f"category {ref.category_id} ({ref.name})"

    @override
    async def fetch_detail(self, external_id: str) -> object:
        """Download the detail document of a product."""
        return await self._http.get_json(f"/api/products/{external_id}")

    @override
    def parse_detail(self, payload: object) -> DiaProductDetail:
        """Validate a product detail payload."""
        return _validate(DiaProductDetail, payload, "product detail")

    @override
    def normalize_detail(self, raw: DiaProductDetail) -> ProductDetails:
        """Map the detail document to domain detail attributes."""
        ean = raw.ean.strip() if raw.ean else None
        return ProductDetails(
            supermarket=DIA_CODE,
            external_id=raw.id,
            ean=ean if ean and _EAN.fullmatch(ean) else None,
            brand=raw.brand,
        )


def _validate[M: BaseModel](model: type[M], payload: object, what: str) -> M:
    """Validate a payload against a schema, raising a domain schema error on mismatch."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        msg = (
            f"Unexpected DIA {what} payload: {exc.error_count()} errors, "
            f"first at {location or '<root>'}: {first['msg']}"
        )
        raise SourceSchemaError(msg) from exc
```

Replace the `...` in `parse_index` with the real tree walk (see `_walk_sections` in Mercadona).

## `tests/unit/test_dia_scraper.py` (factory part)

```python
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from supermarket_scraper.infrastructure.http.client import AsyncJsonClient, HttpClientConfig
from supermarket_scraper.infrastructure.scrapers.dia.scraper import DiaScraper, DiaSettings
from tests.conftest import BASE_URL, FakeApi, load_fixture, no_sleep

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def dia_api() -> FakeApi:
    return FakeApi(
        responses={
            "/api/categories": load_fixture("dia/categories.json"),
            "/api/categories/123/products": load_fixture("dia/category_123.json"),
        }
    )


@pytest.fixture
def make_dia_scraper(dia_api: FakeApi, http_config: HttpClientConfig) -> Callable[[], DiaScraper]:
    def factory() -> DiaScraper:
        client = AsyncJsonClient(
            BASE_URL, http_config, transport=dia_api.transport(), sleep=no_sleep
        )
        return DiaScraper(client, DiaSettings(base_url=BASE_URL))

    return factory
```
