# syntax=docker/dockerfile:1
# 개발과 운영이 같은 베이스를 쓴다. 차이는 코드 마운트와 리로드뿐이다.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 레이어를 소스와 분리한다. 소스만 바뀌면 재설치하지 않는다.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project apps/api --frozen --no-install-project --no-dev

COPY apps/api/src ./src
COPY apps/api/alembic.ini ./alembic.ini
COPY packages/i18n ./packages/i18n

ENV PYTHONPATH=/app/src

# ── 개발: 핫리로드. 소스는 compose 가 마운트한다 ────────────────
FROM base AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project apps/api --frozen --no-install-project
EXPOSE 8000
CMD ["uvicorn", "ieum.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── 운영: 비루트로 돌린다 ───────────────────────────────────────
FROM base AS runtime
RUN useradd --create-home --uid 10001 ieum \
 && chown -R ieum:ieum /app
USER ieum
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "ieum.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
