# syntax=docker/dockerfile:1
# 개발과 운영이 같은 베이스를 쓴다. 차이는 코드 마운트와 리로드뿐이다.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# 내보내기(B17)가 요구하는 시스템 라이브러리와 글꼴.
#
# WeasyPrint 는 pango·cairo·harfbuzz 로 조판한다. 파이썬 패키지만 깔면
# import 에서 터진다 — 그건 그나마 눈에 보이는 실패다.
#
# **글꼴이 없는 쪽이 더 위험하다.** 그때 WeasyPrint 는 터지지 않고 사각형(□)을
# 그린 멀쩡한 PDF 를 준다. 한국어 문서를 내보낸 사람은 파일을 열고 나서야
# 알고, 우리 로그에는 아무것도 안 남는다. 그래서 CJK 글꼴은 선택이 아니라
# 이 기능의 일부다.
#
# `fonts-noto-cjk` 는 무겁다(~100MB). 한국어·일본어·중국어를 한 글꼴로
# 덮으므로 대안(나눔 + 다른 언어별 글꼴)보다 관리할 것이 적다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl ca-certificates \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
      libcairo2 libgdk-pixbuf-2.0-0 \
      fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 레이어를 소스와 분리한다. 소스만 바뀌면 재설치하지 않는다.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
# `ca` 시크릿은 **있으면 쓴다.** TLS 를 다시 끝내는 프록시 뒤에서 빌드하면
# (사내 프록시·CI 의 egress 프록시) 빌드 컨테이너가 그 CA 를 믿어야 PyPI 를
# 받을 수 있다 — `apt` 는 평문 HTTP 라 통과하고 이 단계만 인증서에서 막힌다.
#
#   docker build --secret id=ca,src=/path/to/ca-bundle.crt ...
#
# 없으면 그냥 지나간다(`required=false`). 검증을 끄지는 않는다.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    sh -c 'if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
     uv sync --project apps/api --frozen --no-install-project --no-dev'

COPY apps/api/src ./src
COPY apps/api/alembic.ini ./alembic.ini
COPY packages/i18n ./packages/i18n

ENV PYTHONPATH=/app/src

# **앱이 자기 판을 말할 수 있게 한다.**
#
# 위의 `uv sync` 는 `--no-install-project` 로 돈다(그 시점에 소스가 아직
# 없다). 그래서 `ieum-api` 의 dist-info 가 venv 에 없고,
# `importlib.metadata.version("ieum-api")` 가 `PackageNotFoundError` 로 떨어져
# `/openapi.json` 이 판을 **`0.0.0`** 이라고 말한다. 실제로 그렇게 말하는 것을
# 돌려 보고 알았다 — 테스트는 프로젝트가 설치된 개발 트리에서 도니까 통과했고,
# 이미지만 틀렸다.
#
# 소스를 `/app/src` 에 두는 것은 compose 가 거기 마운트하기 때문에 못 바꾼다.
# 그래서 프로젝트가 기대하는 자리(`apps/api/src`, `packages = ["src/ieum"]`)를
# 심볼릭 링크로 만들어 주고 프로젝트만 설치한다. 코드 사본은 하나다 —
# uv 가 워크스페이스 프로젝트를 editable 로 넣으므로 결국 `/app/src` 를 가리킨다.
RUN ln -s /app/src /app/apps/api/src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    sh -c 'if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
     uv sync --project apps/api --frozen --no-dev'

# ── 개발: 핫리로드. 소스는 compose 가 마운트한다 ────────────────
FROM base AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    sh -c 'if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
     uv sync --project apps/api --frozen'
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
