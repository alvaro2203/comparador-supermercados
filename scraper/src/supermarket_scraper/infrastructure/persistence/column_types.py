from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, override

from sqlalchemy import BigInteger, DateTime, Numeric
from sqlalchemy.types import TypeDecorator, TypeEngine

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

_SCALED_INTEGER_DIALECTS = frozenset({"sqlite"})


class ExactDecimal(TypeDecorator[Decimal]):
    """Exact fixed-point decimal portable across every SQL engine.

    Engines with a native ``NUMERIC`` type store the value as such. Engines
    without exact decimals (SQLite) store it as a scaled big integer, so no
    precision is ever lost to floating point and ordering stays correct.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 14, scale: int = 4) -> None:
        """Configure total digits and fractional digits."""
        super().__init__()
        self.precision = precision
        self.scale = scale
        self._quantum = Decimal(1).scaleb(-scale)

    @override
    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Pick the physical column type for the active dialect."""
        if dialect.name in _SCALED_INTEGER_DIALECTS:
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    @override
    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> object:
        """Quantize the value and convert it to the physical representation."""
        if value is None:
            return None
        quantized = value.quantize(self._quantum)
        if dialect.name in _SCALED_INTEGER_DIALECTS:
            return int(quantized.scaleb(self.scale))
        return quantized

    @override
    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        """Rebuild a ``Decimal`` from the physical representation."""
        if value is None:
            return None
        if dialect.name in _SCALED_INTEGER_DIALECTS:
            return Decimal(str(value)).scaleb(-self.scale).quantize(self._quantum)
        return Decimal(str(value)).quantize(self._quantum)


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware timestamp always stored and returned in UTC."""

    impl = DateTime
    cache_ok = True

    @override
    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Use a timezone-aware column where the engine supports it."""
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reject naive datetimes and normalize to UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "Naive datetimes are not allowed; pass a timezone-aware value"
            raise ValueError(msg)
        utc_value = value.astimezone(UTC)
        if dialect.name == "postgresql":
            return utc_value
        return utc_value.replace(tzinfo=None)

    @override
    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach the UTC timezone to values read back from the database."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
