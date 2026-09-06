"""애플리케이션 설정. 비밀정보는 전부 여기를 경유한다 (CLAUDE.md 절대규칙 7)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# 저장소 루트. src/ieum/config.py → apps/api/src/ieum → 4단계 위.
REPO_ROOT = Path(__file__).resolve().parents[4]


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
    s3_region: str = "us-east-1"
    s3_bucket: str = "ieum-attachments"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_tls: bool = False
    mail_from: str = "ieum@example.com"

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
    supported_locales: tuple[str, ...] = ("en", "ko")

    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 1회 로드."""
    return Settings()
