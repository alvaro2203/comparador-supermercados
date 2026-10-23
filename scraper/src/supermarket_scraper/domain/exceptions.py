class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class InvalidEntityError(DomainError):
    """Raised when an entity is built with values that break its invariants."""


class ScraperError(DomainError):
    """Base class for errors raised while extracting data from a supermarket."""


class SourceUnavailableError(ScraperError):
    """Raised when a supermarket source cannot be reached after all retries."""


class SourceSchemaError(ScraperError):
    """Raised when a supermarket payload no longer matches the expected schema."""


class EmptyCatalogError(ScraperError):
    """Raised when a scrape yields no products, which signals a broken source."""


class UnknownSupermarketError(DomainError):
    """Raised when a supermarket code has no registered scraper."""


class NoPriceDataError(DomainError):
    """Raised when a report is asked for a supermarket or day with no stored prices."""
