"""애플리케이션 설정. 비밀정보는 전부 여기를 경유한다 (CLAUDE.md 절대규칙 7)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]
SearchBackendName = Literal["postgres", "opensearch"]


def _repo_root() -> Path:
    """`.env` 와 i18n 카탈로그를 찾을 기준 경로.

    **단계 수를 세지 않는다.** 소스 체크아웃은 `apps/api/src/ieum/config.py`
    라 4단계 위가 루트지만, 컨테이너 이미지는 `/app/src/ieum/config.py` 로
    평평해서 4단계 위가 아예 없다 — 세어 올라가면 IndexError 로 import 가
    터지고 API 와 워커가 부팅조차 못 한다(실제로 그랬다).

    대신 찾는 것이 실제로 있는 곳을 기준으로 삼는다. `packages/i18n` 은 양쪽
    레이아웃 모두에서 루트 바로 아래에 있다(Dockerfile 이 그렇게 복사한다).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "packages" / "i18n").is_dir():
            return parent
    # 못 찾으면 소스 레이아웃으로 가정한다. 두 경로 모두 환경변수로 덮을 수 있다.
    return here.parents[4] if len(here.parents) > 4 else here.parents[-1]


#: `.env`·i18n 카탈로그의 기준. 환경변수로 덮어쓸 수 있다.
REPO_ROOT = _repo_root()


def _split_commas(v: object) -> object:
    """`a,b` 를 목록으로 읽는다.

    `NoDecode` 가 없으면 pydantic-settings 가 튜플 필드의 환경변수를 JSON 으로
    먼저 파싱하고, 실패하면 `SettingsError` 로 **부팅을 막는다** — 검증기가 돌기
    전이라 검증기로는 못 잡는다. 운영자가 자연스럽게 쓰는 형태(그리고 MinIO 가
    같은 값을 받는 형태)는 쉼표 목록이므로, 디코딩을 꺼 두고 여기서 받는다.
    JSON 배열도 그대로 통한다.
    """
    if isinstance(v, str):
        text = v.strip()
        if text.startswith("["):
            return json.loads(text)
        return tuple(part.strip() for part in text.split(",") if part.strip())
    return v


#: 쉼표로 나열하는 문자열 목록. 환경변수에 JSON 을 쓰게 하지 않는다.
CommaList = Annotated[tuple[str, ...], NoDecode, BeforeValidator(_split_commas)]


class Settings(BaseSettings):
    """환경변수 기반 설정. 접두사 `IEUM_`."""

    model_config = SettingsConfigDict(
        env_prefix="IEUM_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = "development"
    debug: bool = False
    base_url: str = "http://localhost:5173"
    #: API 가 **밖에서 보이는** 주소. SAML 은 IdP 가 우리 ACS 로 직접 POST 하고
    #: 메타데이터에 그 주소를 적어 두므로, 화면 주소로는 안 된다. 운영에서는
    #: 보통 화면과 같은 오리진(`/api/v1`)이라 비워 두면 `base_url` 을 쓴다 —
    #: 개발 스택처럼 포트가 갈린 곳에서만 따로 준다.
    api_url: str | None = None

    # 이 키에서 애플리케이션 레벨 암호화 키를 파생한다 (D-15).
    secret_key: SecretStr = SecretStr("")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ieum"
    test_database_url: str | None = None
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    slow_query_ms: int = 200

    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str | None = None
    #: presigned URL 에 쓸 **브라우저가 볼 수 있는** 주소. 비우면 위와 같다.
    #: 컨테이너 안팎에서 스토리지 주소가 다를 때만 필요하다 — compose 로
    #: 띄우면 서버는 `http://minio:9000`, 브라우저는 `http://localhost:9000`.
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "ieum-attachments"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")

    # 검색 백엔드 (ADR-0005, ADR-0015)
    #: `postgres`(기본, PGroonga) 또는 `opensearch`.
    #:
    #: **바꿔도 색인은 여전히 Postgres 에 쓴다.** OpenSearch 는 그 표를 비추는
    #: 읽기 쪽이다 — 그래서 되돌릴 때 되색인이 필요 없고, OpenSearch 가
    #: 죽어도 이슈 저장이 실패하지 않는다.
    search_backend: SearchBackendName = "postgres"
    opensearch_url: str | None = None
    opensearch_index: str = "ieum-search"
    #: 본문에 쓸 분석기. 기본값 `cjk` 는 루씬에 들어 있는 것이라 **플러그인
    #: 없이** 돈다 — 한국어를 두 글자씩 쪼갠다(`문서로` → `문서`·`서로`).
    #: 되찾기는 되지만 헛맞음이 생긴다. `analysis-nori` 를 설치했다면
    #: `nori` 로 바꾼다 — 그쪽이 형태소를 안다.
    opensearch_analyzer: str = "cjk"
    opensearch_username: str | None = None
    opensearch_password: SecretStr = SecretStr("")

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_tls: bool = False
    #: 릴레이가 인증을 요구하면 이 둘이 있어야 한다.
    #:
    #: **설치본은 처음부터 이 둘을 `.env` 에 적어 주고 있었는데 앱에 자리가
    #: 없었다.** 그래서 깐 사람은 값을 채워 넣고도 메일이 안 나갔고, 릴레이는
    #: 535 를 돌려주는데 화면에는 아무 말도 없었다 — 초대·알림·다이제스트가
    #: 통째로 조용히 멈추는 모양이다.
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    mail_from: str = "ieum@example.com"

    #: 웹훅을 **사설·루프백 주소로도** 보낼 수 있게 할지.
    #:
    #: 기본은 막는다. 웹훅은 우리 서버가 대신 요청을 보내 주는 기능이고,
    #: 전송 기록에 응답 본문 앞부분이 남아 화면에 보인다 — 둘을 합치면
    #: 내부 주소를 아무거나 열어 읽는 도구가 된다(클라우드 메타데이터가
    #: 제일 나쁜 경우다).
    #:
    #: 사내망 수신처로 보내야 하는 설치본만 켠다. 켜면 **그 위험을 아는
    #: 사람이 켠 것**이어야 한다.
    webhook_allow_private_targets: bool = False

    # 인증 정책 (docs/architecture/auth.md 1절)
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    step_up_window_seconds: int = 300
    password_min_length: int = 12
    login_max_attempts: int = 10
    login_attempt_window_seconds: int = 900

    # argon2id 파라미터. 상향하면 로그인 시 자동 리해시된다.
    argon2_memory_cost: int = 65_536  # 64MB
    argon2_time_cost: int = 3
    argon2_parallelism: int = 4

    # i18n 카탈로그 위치. 프론트·백엔드가 같은 파일을 본다.
    i18n_catalog_dir: Path = Field(default=REPO_ROOT / "packages" / "i18n")
    default_locale: str = "en"
    supported_locales: CommaList = ("en", "ko")

    #: 브라우저 앱이 열리는 오리진. 여기 없는 오리진에서 온 요청은 프리플라이트에서
    #: 막힌다. 개발 기본값은 Vite 가 뜨는 두 주소다 — `localhost` 와 `127.0.0.1` 은
    #: 브라우저에게 서로 다른 오리진이라, 한쪽만 넣어 두면 다른 쪽으로 연 사람이
    #: 로그인부터 실패한다.
    cors_origins: CommaList = ("http://localhost:5173", "http://127.0.0.1:5173")

    @model_validator(mode="after")
    def _opensearch_needs_a_url(self) -> Settings:
        """백엔드를 골라 놓고 주소를 안 주면 **기동에서** 멈춘다.

        안 멈추면 앱은 멀쩡히 뜨고 검색만 조용히 0건이 된다. 검색이 0건인
        것은 "없다" 와 구별되지 않아서, 아무도 고장이라고 생각하지 않는다.
        """
        if self.search_backend == "opensearch" and not (self.opensearch_url or "").strip():
            raise ValueError("IEUM_SEARCH_BACKEND=opensearch 면 IEUM_OPENSEARCH_URL 이 필요하다.")
        return self

    @field_validator("secret_key")
    @classmethod
    def _secret_key_strong_enough(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if len(raw) < 32:
            raise ValueError(
                "IEUM_SECRET_KEY 는 32자 이상이어야 한다. `make secret` 으로 생성한다."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def public_api_url(self) -> str:
        """IdP 가 우리를 부를 때 쓰는 주소. 비어 있으면 화면 주소와 같은 곳이다."""
        return (self.api_url or self.base_url).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 1회 로드."""
    return Settings()
