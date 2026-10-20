"""2FA 시드 관리자는 **운영에서 안 만들어진다.**

`SEED_MFA_ADMIN_*` 은 개발 스택과 E2E 가 쓰는 계정이고, 비밀번호와 TOTP
시크릿이 저장소에 적혀 있다(`docker-compose.yml`). 운영 오버라이드
(`deploy/compose/prod.yml`)가 그 값을 안 지우던 동안, 운영 컨테이너에서 시드를
한 번 돌리면 **누구나 아는 비밀번호와 2FA 코드로 들어오는 관리자**가 생겼다.

오버라이드는 이제 빈 값을 박는다(`test_install_agrees.py` 가 병합 결과를 본다).
여기서는 그 한 겹이 또 빠져도 시드가 막는지를 본다.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.modules.identity.repository import UserRepository
from ieum.seed import seed

MFA_EMAIL = "mfa-admin-guard@example.com"


@pytest.fixture
def _mfa_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_MFA_ADMIN_EMAIL", MFA_EMAIL)
    monkeypatch.setenv("SEED_MFA_ADMIN_PASSWORD", "seed-mfa-admin-password-1234")
    monkeypatch.setenv("SEED_MFA_ADMIN_TOTP_SECRET", "IEUMDEVSEEDTOTPSECRET234")


@pytest.mark.usefixtures("_mfa_env")
async def test_production_does_not_seed_the_known_mfa_admin(
    session: AsyncSession, settings: Settings
) -> None:
    production = settings.model_copy(update={"env": "production"})
    await seed(session, production)
    assert await UserRepository(session).get_by_email(MFA_EMAIL) is None


@pytest.mark.usefixtures("_mfa_env")
async def test_development_still_seeds_it(session: AsyncSession, settings: Settings) -> None:
    """위 단언이 "아무것도 안 만드는 시드" 로 참이 되는 것을 막는다."""
    development = settings.model_copy(update={"env": "development"})
    await seed(session, development)
    assert await UserRepository(session).get_by_email(MFA_EMAIL) is not None
