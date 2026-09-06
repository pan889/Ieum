"""설정 로딩. 특히 배포 레이아웃에서 깨지지 않는지."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
