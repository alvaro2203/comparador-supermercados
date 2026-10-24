from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _loose_decimal(value: object) -> object:
    """Accept numbers or padded numeric strings, mapping blanks to ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip().replace(",", ".")
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except InvalidOperation:
            return None
    return value


def _blank_to_none(value: object) -> object:
    """Normalize whitespace-only strings to ``None``."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


type LooseDecimal = Annotated[Decimal | None, BeforeValidator(_loose_decimal)]
type OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class MercadonaModel(BaseModel):
    """Base for Mercadona payloads: tolerant to unknown fields, immutable once parsed."""

    model_config = ConfigDict(extra="ignore", frozen=True, coerce_numbers_to_str=True)


class SubcategoryRef(MercadonaModel):
    """Second-level category as listed in the category index."""

    id: int
    name: str
    order: int = 0
    published: bool = True


class TopCategory(MercadonaModel):
    """First-level category of the index, with its children."""

    id: int
    name: str
    order: int = 0
    published: bool = True
    categories: list[SubcategoryRef] = Field(default_factory=list)


class CategoryIndexResponse(MercadonaModel):
    """Payload of ``GET /api/categories/``."""

    results: list[TopCategory]


class PriceInstructions(MercadonaModel):
    """Pricing and packaging block of a Mercadona product."""

    unit_price: Decimal
    bulk_price: LooseDecimal = None
    reference_price: LooseDecimal = None
    reference_format: OptionalText = None
    previous_unit_price: LooseDecimal = None
    price_decreased: bool = False
    iva: LooseDecimal = None
    tax_percentage: LooseDecimal = None
    is_new: bool = False
    is_pack: bool = False
    pack_size: LooseDecimal = None
    unit_size: LooseDecimal = None
    unit_name: OptionalText = None
    size_format: OptionalText = None
    total_units: int | None = None
    approx_size: bool = False
    selling_method: int | None = None
    bunch_selector: bool = False

    @property
    def vat_rate(self) -> Decimal | None:
        """Return the VAT percentage: ``tax_percentage``, or ``iva`` when it is missing.

        Mercadona fills ``tax_percentage`` on every product and ``iva`` on about a
        quarter of them; when both are present they agree.
        """
        return self.tax_percentage if self.tax_percentage is not None else self.iva


class ListedProduct(MercadonaModel):
    """Product entry as it appears inside a category page."""

    id: str
    display_name: str
    slug: OptionalText = None
    packaging: OptionalText = None
    published: bool = True
    is_new_arrival: bool
    share_url: OptionalText = None
    thumbnail: OptionalText = None
    unavailable_from: OptionalText = None
    price_instructions: PriceInstructions


class Section(MercadonaModel):
    """Third-level (or deeper) section of a category page holding products."""

    id: int
    name: str
    order: int = 0
    products: list[ListedProduct] = Field(default_factory=list)
    categories: list[Section] = Field(default_factory=list)


class CategoryPageResponse(MercadonaModel):
    """Payload of ``GET /api/categories/{id}/``."""

    id: int
    name: str
    categories: list[Section] = Field(default_factory=list)


class DetailBlock(MercadonaModel):
    """Descriptive attributes of a product detail."""

    brand: OptionalText = None
    origin: OptionalText = None
    description: OptionalText = None
    legal_name: OptionalText = None


class NutritionInformation(MercadonaModel):
    """Ingredients and allergens of a product detail."""

    allergens: OptionalText = None
    ingredients: OptionalText = None


class Photo(MercadonaModel):
    """One product image in several resolutions."""

    zoom: OptionalText = None
    regular: OptionalText = None
    thumbnail: OptionalText = None


class ProductDetailResponse(MercadonaModel):
    """Payload of ``GET /api/products/{id}/``."""

    id: str
    ean: OptionalText = None
    brand: OptionalText = None
    origin: OptionalText = None
    details: DetailBlock | None = None
    nutrition_information: NutritionInformation | None = None
    photos: list[Photo] = Field(default_factory=list)
