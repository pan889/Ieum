"""초기 데이터. `make seed` / `python -m ieum.cli seed` 가 호출한다.

멱등이다. 여러 번 돌려도 같은 상태가 된다.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.core.crypto import PasswordHashingService
from ieum.core.logging import configure_logging, get_logger
from ieum.core.permissions import Scope, ScopeKind
from ieum.db.session import init_engine, session_scope
from ieum.modules.identity import permissions as identity_perms
from ieum.modules.identity.models import User
from ieum.modules.identity.repository import UserRepository, normalize_email
from ieum.modules.org import permissions as org_perms
from ieum.modules.org.repository import RoleRepository
from ieum.modules.org.service import WorkspaceService

log = get_logger(__name__)

#: 내장 역할. 이름은 설치 시 locale 에 맞춰 만들고 이후 사용자가 바꿀 수 있다.
BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Administrator": (
        "global",
        (*identity_perms.ALL, *org_perms.ALL),
    ),
    "Member": (
        "global",
        (identity_perms.USER_VIEW, org_perms.PROJECT_VIEW),
    ),
    "Project Admin": (
        "project",
        (
            org_perms.PROJECT_VIEW,
            org_perms.PROJECT_EDIT,
            org_perms.PROJECT_ARCHIVE,
            org_perms.PROJECT_ADMIN,
            org_perms.ROLE_ASSIGN,
        ),
    ),
    "Project Member": ("project", (org_perms.PROJECT_VIEW,)),
}


async def seed(session: AsyncSession, settings: Settings) -> None:
    workspace = await WorkspaceService(session).ensure(name="Ieum")
    log.info("seed.workspace", id=str(workspace.id))

    roles = RoleRepository(session)
    created_roles = {}
    for name, (scope_kind, grants) in BUILTIN_ROLES.items():
        role = await roles.get_by_name(name, scope_kind)
        if role is None:
            from ieum.modules.org.models import Role

            role = Role(
                name=name,
                scope_kind=scope_kind,
                is_builtin=True,
                description=f"{name} (내장)",
            )
            roles.add(role)
            await session.flush()
            for permission in grants:
                roles.grant(role.id, permission)
            log.info("seed.role_created", name=name, grants=len(grants))
        created_roles[name] = role

    email = normalize_email(os.getenv("SEED_ADMIN_EMAIL", "admin@example.com"))
    password = os.getenv("SEED_ADMIN_PASSWORD")

    users = UserRepository(session)
    admin = await users.get_by_email(email)
    if admin is None:
        if not password:
            log.warning("seed.admin_skipped", reason="SEED_ADMIN_PASSWORD 가 없다")
            return
        hasher = PasswordHashingService(
            memory_cost=settings.argon2_memory_cost,
            time_cost=settings.argon2_time_cost,
            parallelism=settings.argon2_parallelism,
        )
        admin = User(
            email=email,
            display_name="Administrator",
            status="active",
            password_hash=hasher.hash(password),
            locale=settings.default_locale,
        )
        users.add(admin)
        await session.flush()
        log.info("seed.admin_created", email=email)

    admin_role = created_roles["Administrator"]
    existing = await session.get(type(admin_role), admin_role.id)
    assert existing is not None
    from sqlalchemy import select

    from ieum.modules.org.models import RoleAssignment

    stmt = select(RoleAssignment).where(
        RoleAssignment.role_id == admin_role.id,
        RoleAssignment.principal_id == admin.id,
        RoleAssignment.scope_kind == ScopeKind.GLOBAL.value,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        roles.assign(
            role_id=admin_role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=admin.id,
        )
        log.info("seed.admin_role_assigned")


async def run_seed() -> int:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=False)
    init_engine(settings)
    async with session_scope() as session:
        await seed(session, settings)
    log.info("seed.done")
    return 0
