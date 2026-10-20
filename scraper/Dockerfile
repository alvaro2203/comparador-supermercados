FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    SCRAPER_LOG_FORMAT=json \
    SCRAPER_DATABASE_URL=sqlite:////data/supermarkets.db

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra postgres
COPY src ./src
RUN uv sync --frozen --no-dev --extra postgres

RUN useradd --create-home scraper && mkdir -p /data && chown scraper /data
USER scraper
VOLUME ["/data"]

ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "supermarket-scraper"]
CMD ["run"]
