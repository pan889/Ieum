"""설정 로딩. 특히 배포 레이아웃에서 깨지지 않는지."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from ieum.config import REPO_ROOT, Settings, _repo_root


class TestRepoRoot:
    def test_finds_catalog_in_source_checkout(self) -> None:
        assert (REPO_ROOT / "packages" / "i18n").is_dir()

    def test_finds_catalog_in_flat_container_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """컨테이너 이미지는 `/app/src/ieum/config.py` 로 평평하다.

        단계 수를 세어 올라가면 4단계 위가 없어서 IndexError 가 났고, API 와
        워커가 import 단계에서 죽었다. 회귀로 고정한다.
        """
        app = tmp_path / "app"
        (app / "src" / "ieum").mkdir(parents=True)
        (app / "packages" / "i18n").mkdir(parents=True)
        fake_config = app / "src" / "ieum" / "config.py"
        fake_config.touch()

        monkeypatch.setattr("ieum.config.__file__", str(fake_config))
        assert _repo_root() == app

    def test_does_not_climb_past_the_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """더 위에 또 있어도 가까운 쪽을 쓴다."""
        outer = tmp_path / "outer"
        (outer / "packages" / "i18n").mkdir(parents=True)
        inner = outer / "app"
        (inner / "src" / "ieum").mkdir(parents=True)
        (inner / "packages" / "i18n").mkdir(parents=True)
        fake_config = inner / "src" / "ieum" / "config.py"
        fake_config.touch()

        monkeypatch.setattr("ieum.config.__file__", str(fake_config))
        assert _repo_root() == inner

    def test_survives_when_catalog_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """카탈로그를 못 찾아도 예외로 죽지는 않는다 — 경로는 환경변수로 덮는다."""
        stray = tmp_path / "a" / "b" / "c" / "config.py"
        stray.parent.mkdir(parents=True)
        stray.touch()

        monkeypatch.setattr("ieum.config.__file__", str(stray))
        assert isinstance(_repo_root(), Path)


class TestSettings:
    def test_catalog_dir_defaults_under_repo_root(self) -> None:
        settings = Settings(secret_key="x" * 32)  # type: ignore[arg-type]
        assert settings.i18n_catalog_dir == REPO_ROOT / "packages" / "i18n"

    def test_env_overrides_catalog_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 배포마다 카탈로그 위치가 다를 수 있다. 계산값에 갇히면 안 된다.
        monkeypatch.setenv("IEUM_I18N_CATALOG_DIR", "/somewhere/else")
        assert Settings(secret_key="x" * 32).i18n_catalog_dir == Path(  # type: ignore[arg-type]
            "/somewhere/else"
        )


class TestSeedEntryPointsStandAlone:
    """시드 모듈만 import 해도 매퍼가 완성돼야 한다.

    모델을 일부만 import 하면 FK 가 가리키는 테이블이 매퍼에 없어서, **빈
    DB 에 실제로 넣는 순간** NoReferencedTableError 로 죽는다. 이미 값이
    있으면 flush 가 없어 안 터지므로 두 번째 실행부터는 멀쩡해 보인다 —
    그래서 신규 설치에서만 터진다(실제로 `make dev` 에서 그랬다).

    DB 없이 잡는다. `sorted_tables` 가 flush 와 똑같이 FK 를 풀어 본다
    (`configure_mappers()` 로는 안 잡힌다 — 매퍼 설정은 FK 대상 테이블까지
    확인하지 않는다).
    """

    @pytest.mark.parametrize("module", ["ieum.seed", "ieum.demo_fields", "ieum.cli"])
    def test_foreign_keys_resolve(self, module: str) -> None:
        code = f"import {module}\nfrom ieum.db.base import Base\nBase.metadata.sorted_tables\n"
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


class TestCommaLists:
    """환경변수에 JSON 을 쓰게 하지 않는다.

    pydantic-settings 는 튜플 필드를 JSON 으로 먼저 파싱하고 실패하면
    `SettingsError` 로 **부팅을 막는다**. 검증기보다 먼저 도는 단계라
    `mode="before"` 로는 못 잡는다 — `NoDecode` 로 꺼야 한다.
    """

    def test_comma_separated_origins_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IEUM_CORS_ORIGINS", "https://a.example, https://b.example")
        assert Settings(secret_key="x" * 32).cors_origins == (  # type: ignore[arg-type]
            "https://a.example",
            "https://b.example",
        )

    def test_a_single_origin_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IEUM_CORS_ORIGINS", "https://only.example")
        assert Settings(secret_key="x" * 32).cors_origins == ("https://only.example",)  # type: ignore[arg-type]

    def test_json_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 이미 JSON 으로 적어 둔 배포를 깨지 않는다.
        monkeypatch.setenv("IEUM_CORS_ORIGINS", '["https://a.example"]')
        assert Settings(secret_key="x" * 32).cors_origins == ("https://a.example",)  # type: ignore[arg-type]

    def test_locales_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IEUM_SUPPORTED_LOCALES", "en,ko,ja")
        assert Settings(secret_key="x" * 32).supported_locales == ("en", "ko", "ja")  # type: ignore[arg-type]

    def test_both_dev_hosts_are_allowed_by_default(self) -> None:
        """`localhost` 와 `127.0.0.1` 은 브라우저에게 서로 다른 오리진이다.

        한쪽만 넣어 두면 다른 쪽으로 연 사람이 로그인부터 실패한다 (E2E 가
        127.0.0.1 로 열어서 실제로 그랬다).
        """
        origins = Settings(secret_key="x" * 32).cors_origins  # type: ignore[arg-type]
        assert "http://localhost:5173" in origins
        assert "http://127.0.0.1:5173" in origins


class TestComposeFeedsTheSameOrigins:
    """API 와 MinIO 의 오리진 허용 목록은 **같은 변수**에서 와야 한다.

    둘로 나뉘어 있으면 한쪽만 고쳐 두고 나머지가 프리플라이트에서 막힌다.
    컨테이너를 띄워야만 보이는 종류라 여기서 정적으로 고정한다.
    """

    def _compose(self, name: str) -> Any:
        path = REPO_ROOT / name
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_dev_api_and_minio_share_the_variable(self) -> None:
        compose = self._compose("docker-compose.yml")
        api = compose["x-api-env"]["IEUM_CORS_ORIGINS"]
        minio = compose["services"]["minio"]["environment"]["MINIO_API_CORS_ALLOW_ORIGIN"]
        assert "IEUM_WEB_ORIGINS" in api
        assert "IEUM_WEB_ORIGINS" in minio

    def test_production_requires_the_origins(self) -> None:
        """운영 기본값이 localhost 로 남으면 앱이 통째로 안 뜬다."""
        compose = self._compose("deploy/compose/prod.yml")
        value = compose["services"]["api"]["environment"]["IEUM_CORS_ORIGINS"]
        # `:?` 는 값이 없으면 compose 가 기동을 거부하게 한다.
        assert value.startswith("${IEUM_WEB_ORIGINS:?")


class TestCiUsesTheSameCorsMechanism:
    """CI 의 스토리지도 **환경변수로** CORS 를 맞춰야 한다.

    MinIO 는 S3 의 `PutBucketCors` 를 구현하지 않는다 — 부르면 응답이
    `NotImplemented` 다. CI 가 그걸 불러서 E2E 잡이 버킷 준비 단계에서
    통째로 죽었고, 브라우저 테스트는 **한 번도 돌지 못했다.** 컴포즈는
    처음부터 환경변수를 썼는데 CI 만 다른 길로 갔다.

    잡을 돌려야만 보이는 종류라 워크플로우 파일을 정적으로 읽어 고정한다.
    """

    def _e2e_job(self) -> Any:
        workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
        return workflow["jobs"]["e2e"]

    def _steps(self) -> str:
        return "\n".join(step.get("run", "") for step in self._e2e_job()["steps"])

    def test_ci_does_not_call_the_unimplemented_cors_api(self) -> None:
        body = self._steps()
        assert "put_bucket_cors" not in body
        assert "PutBucketCors" not in body

    def test_ci_storage_allows_the_browser_origin(self) -> None:
        """허용 목록이 없으면 프리사인드 PUT 이 프리플라이트에서 막힌다."""
        body = self._steps()
        assert "MINIO_API_CORS_ALLOW_ORIGIN" in body
        # 브라우저가 실제로 여는 주소(playwright 의 baseURL 기본값).
        origins = next(line for line in body.splitlines() if "MINIO_API_CORS_ALLOW_ORIGIN" in line)
        assert "http://127.0.0.1:5173" in origins
