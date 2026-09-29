FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --prefix=/install ".[postgres]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN groupadd --system relayforge && useradd --system --gid relayforge relayforge
COPY --from=builder /install /usr/local
COPY alembic.ini ./
COPY migrations ./migrations

USER relayforge
EXPOSE 8000
CMD ["relayforge", "api", "--host", "0.0.0.0"]

