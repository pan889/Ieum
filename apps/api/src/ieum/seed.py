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
from ieum.core.time import utcnow
from ieum.db.session import init_engine, session_scope
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.identity import permissions as identity_perms
from ieum.modules.identity.models import User
from ieum.modules.identity.repository import UserRepository, normalize_email
from ieum.modules.issues import permissions as issue_perms
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState, WorkflowTransition
from ieum.modules.issues.repository import IssueTypeRepository, WorkflowRepository
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.notify import permissions as notify_perms
from ieum.modules.org import permissions as org_perms
from ieum.modules.org.models import Role
from ieum.modules.org.repository import RoleRepository
from ieum.modules.org.service import WorkspaceService
from ieum.modules.plugins import permissions as plugin_perms
from ieum.modules.vcs import permissions as vcs_perms
from ieum.modules.wiki import permissions as wiki_perms

log = get_logger(__name__)

#: 내장 역할. 이름은 설치 시 locale 에 맞춰 만들고 이후 사용자가 바꿀 수 있다.
BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Administrator": (
        "global",
        (
            *identity_perms.ALL,
            *org_perms.ALL,
            *issue_perms.ALL,
            *wiki_perms.ALL,
            *desk_perms.ALL,
            # 한동안 빠져 있었다. 웹훅 화면은 M2 에 만들었는데 그 권한을
            # 가진 내장 역할이 없어서, 관리자가 그 화면에서 403 을 봤다.
            # `test_seed_grants.py` 가 이제 그 종류를 붙잡는다.
            *notify_perms.ALL,
            *vcs_perms.ALL,
            # 앱 등록 (M6). 전역이고, 자리를 정하는 쪽은 step-up 이다 —
            # 화면 안에 남의 글을 놓는 자리를 내주는 일이라서 그렇다.
            *plugin_perms.ALL,
        ),
    ),
    "Member": (
        "global",
        (
            identity_perms.USER_VIEW,
            org_perms.PROJECT_VIEW,
            # 자산 목록 (C15). 전역 권한이라 프로젝트 역할(`Desk Agent`)에
            # 담을 수 없고, 상담원이 티켓에 장비를 이으려면 찾을 수 있어야
            # 한다. 재고는 조직의 것이고 비밀이 아니다 — 고객은 역할을
            # 받지 않으므로 이 손잡이가 고객에게 열리지도 않는다.
            desk_perms.ASSET_VIEW,
        ),
    ),
    "Project Admin": (
        "project",
        (
            org_perms.PROJECT_VIEW,
            org_perms.PROJECT_EDIT,
            org_perms.PROJECT_ARCHIVE,
            org_perms.PROJECT_ADMIN,
            org_perms.ROLE_ASSIGN,
            # 프로젝트 관리자는 이슈를 전부 다룰 수 있다.
            issue_perms.ISSUE_VIEW,
            issue_perms.ISSUE_CREATE,
            issue_perms.ISSUE_EDIT,
            issue_perms.ISSUE_TRANSITION,
            issue_perms.ISSUE_ASSIGN,
            issue_perms.ISSUE_DELETE,
            issue_perms.ISSUE_LINK,
            issue_perms.COMMENT_ADD,
            issue_perms.COMMENT_EDIT_ANY,
            issue_perms.COMMENT_VIEW_INTERNAL,
            issue_perms.WORKLOG_ADD,
            issue_perms.WORKLOG_EDIT_ANY,
            issue_perms.SECURITY_LEVEL_SET,
            issue_perms.BOARD_MANAGE,
            # 스프린트는 계획 회의에서 만든다 — 프로젝트를 굴리는 사람의 일이다.
            issue_perms.SPRINT_MANAGE,
            # 프로젝트 관리자는 자기 프로젝트의 웹훅을 본다. 만드는 것은
            # 데이터를 외부로 내보내는 일이라 step-up 대상이다.
            notify_perms.WEBHOOK_VIEW,
            notify_perms.WEBHOOK_MANAGE,
            # 큐는 보드와 같은 종류의 것이다 — 프로젝트 관리자가 자기
            # 프로젝트의 작업 목록을 정한다. 포털 정의(`portal.manage`)는
            # 여기 넣지 않았다: 그건 고객이 보는 화면이고, 누가 고쳐야 하는지는
            # 따로 정할 문제다.
            desk_perms.QUEUE_MANAGE,
            # 저장소 연동은 웹훅과 같은 결이다 — 자기 프로젝트의 코드가
            # 어디에 있는지는 프로젝트를 굴리는 사람이 안다. 등록이
            # step-up 대상인 이유는 `vcs/permissions.py` 에 적어 뒀다.
            vcs_perms.REPO_VIEW,
            vcs_perms.REPO_MANAGE,
        ),
    ),
    "Project Member": (
        "project",
        (org_perms.PROJECT_VIEW, *issue_perms.MEMBER_DEFAULTS),
    ),
    # 고객 포털이 아니라 내부 열람 전용. 데스크 고객 계정은 역할을 받지
    # 않는다 — 포털이 권한의 근거다 (desk/service.py).
    "Project Viewer": (
        "project",
        (org_perms.PROJECT_VIEW, issue_perms.ISSUE_VIEW),
    ),
    # 상담원. 티켓을 다루지만 포털 정의는 못 고친다 — 폼을 바꾸는 일은
    # 고객이 보는 화면을 바꾸는 일이라 관리자의 몫이다.
    "Desk Agent": (
        "project",
        (
            org_perms.PROJECT_VIEW,
            issue_perms.ISSUE_VIEW,
            issue_perms.ISSUE_CREATE,
            issue_perms.ISSUE_EDIT,
            issue_perms.ISSUE_TRANSITION,
            issue_perms.ISSUE_ASSIGN,
            issue_perms.ISSUE_LINK,
            issue_perms.COMMENT_ADD,
            issue_perms.COMMENT_EDIT_OWN,
            # 상담원은 내부 노트를 본다. 이것이 고객과의 결정적 차이다.
            issue_perms.COMMENT_VIEW_INTERNAL,
            issue_perms.WORKLOG_ADD,
            *desk_perms.AGENT_DEFAULTS,
        ),
    ),
    "Space Admin": (
        "space",
        (
            wiki_perms.PAGE_VIEW,
            wiki_perms.PAGE_CREATE,
            wiki_perms.PAGE_EDIT,
            wiki_perms.PAGE_DELETE,
            wiki_perms.PAGE_MOVE,
            wiki_perms.PAGE_RESTRICT,
            wiki_perms.COMMENT_ADD,
            wiki_perms.COMMENT_EDIT_ANY,
            wiki_perms.SPACE_ADMIN,
        ),
    ),
    "Space Editor": (
        "space",
        (
            wiki_perms.PAGE_VIEW,
            wiki_perms.PAGE_CREATE,
            wiki_perms.PAGE_EDIT,
            wiki_perms.COMMENT_ADD,
            wiki_perms.COMMENT_EDIT_OWN,
        ),
    ),
    "Space Viewer": (
        "space",
        (wiki_perms.PAGE_VIEW, wiki_perms.COMMENT_ADD, wiki_perms.COMMENT_EDIT_OWN),
    ),
}

DEFAULT_WORKFLOW_NAME = "Default"

#: (이름, 아이콘, 하위작업 여부)
DEFAULT_ISSUE_TYPES: tuple[tuple[str, str, bool], ...] = (
    ("Task", "task", False),
    ("Bug", "bug", False),
    ("Story", "story", False),
    ("Sub-task", "subtask", True),
)


async def _seed_workflow(session: AsyncSession) -> Workflow:
    """기본 워크플로우와 전이. 멱등하다."""
    repo = WorkflowRepository(session)
    workflow = await repo.get_by_name(DEFAULT_WORKFLOW_NAME)
    if workflow is not None:
        return workflow

    workflow = Workflow(
        name=DEFAULT_WORKFLOW_NAME,
        description="Open → In Progress → Resolved → Closed (내장)",
        is_builtin=True,
    )
    repo.add(workflow)
    await session.flush()

    states: dict[str, WorkflowState] = {}
    for position, (name, category, is_initial) in enumerate(DEFAULT_WORKFLOW_STATES):
        state = WorkflowState(
            workflow_id=workflow.id,
            name=name,
            category=category,
            position=position,
            is_initial=is_initial,
        )
        session.add(state)
        states[name] = state
    await session.flush()

    for position, (name, from_name, to_name, post) in enumerate(DEFAULT_TRANSITIONS):
        session.add(
            WorkflowTransition(
                workflow_id=workflow.id,
                name=name,
                from_state_id=states[from_name].id if from_name else None,
                to_state_id=states[to_name].id,
                conditions=[],
                post_functions=post,
                position=position,
            )
        )
    await session.flush()
    log.info(
        "seed.workflow_created",
        name=DEFAULT_WORKFLOW_NAME,
        states=len(states),
        transitions=len(DEFAULT_TRANSITIONS),
    )
    return workflow


async def _seed_issue_types(session: AsyncSession, workflow: Workflow) -> None:
    """전역 이슈 유형. project_id 가 NULL 이라 모든 프로젝트에서 쓴다."""
    repo = IssueTypeRepository(session)
    from sqlalchemy import select as _select

    existing = {
        t.name
        for t in (
            await session.execute(_select(IssueType).where(IssueType.project_id.is_(None)))
        ).scalars()
    }
    created = 0
    for position, (name, icon, is_subtask) in enumerate(DEFAULT_ISSUE_TYPES):
        if name in existing:
            continue
        repo.add(
            IssueType(
                project_id=None,
                name=name,
                icon=icon,
                is_subtask=is_subtask,
                workflow_id=workflow.id,
                position=position,
            )
        )
        created += 1
    if created:
        await session.flush()
        log.info("seed.issue_types_created", count=created)


async def _seed_builtin_roles(session: AsyncSession) -> dict[str, Role]:
    """내장 역할과 권한을 **정의에 맞게 동기화**한다.

    생성만 하고 끝내면 안 된다. 새 모듈이 권한을 추가했을 때 기존 설치의
    내장 역할이 옛 권한 집합에 머물러, 업그레이드해도 관리자가 새 기능을
    못 쓰는 상태가 된다. 내장 역할은 시스템 소유이므로 매 시드마다 맞춘다.
    사용자가 만든 역할은 건드리지 않는다.
    """
    from sqlalchemy import select as _select

    from ieum.modules.org.models import PermissionGrant, Role

    roles = RoleRepository(session)
    result: dict[str, Role] = {}

    for name, (scope_kind, grants) in BUILTIN_ROLES.items():
        role = await roles.get_by_name(name, scope_kind)
        if role is None:
            role = Role(
                name=name,
                scope_kind=scope_kind,
                is_builtin=True,
                description=f"{name} (내장)",
            )
            roles.add(role)
            await session.flush()
            log.info("seed.role_created", name=name)

        current = {
            g.permission: g
            for g in (
                await session.execute(
                    _select(PermissionGrant).where(PermissionGrant.role_id == role.id)
                )
            ).scalars()
        }
        wanted = set(grants)

        added = 0
        for permission in sorted(wanted - set(current)):
            roles.grant(role.id, permission)
            added += 1
        # 정의에서 빠진 권한은 회수한다. 남겨두면 "왜 아직 되지?"가 생긴다.
        removed = 0
        for permission in sorted(set(current) - wanted):
            await session.delete(current[permission])
            removed += 1

        if added or removed:
            await session.flush()
            log.info("seed.role_synced", name=name, added=added, removed=removed)

        result[name] = role

    return result


async def seed(session: AsyncSession, settings: Settings) -> None:
    workspace = await WorkspaceService(session).ensure(name="Ieum")
    log.info("seed.workspace", id=str(workspace.id))

    workflow = await _seed_workflow(session)
    await _seed_issue_types(session, workflow)

    created_roles = await _seed_builtin_roles(session)

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

    from sqlalchemy import select

    from ieum.modules.org.models import RoleAssignment

    stmt = select(RoleAssignment).where(
        RoleAssignment.role_id == admin_role.id,
        RoleAssignment.principal_id == admin.id,
        RoleAssignment.scope_kind == ScopeKind.GLOBAL.value,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        RoleRepository(session).assign(
            role_id=admin_role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=admin.id,
        )
        log.info("seed.admin_role_assigned")

    await _seed_mfa_admin(session, settings, admin_role)


async def _seed_mfa_admin(session: AsyncSession, settings: Settings, admin_role: Role) -> None:
    """2FA 를 갖춘 관리자 — **개발 스택 전용.**

    step-up 이 필요한 설정(역할·IdP·워크플로우·웹훅)은 실제로 2FA 를 통과한
    세션만 만질 수 있다(auth.md 3·6절). 부트스트랩 관리자는 인증기를 직접
    등록해야 그 문을 지나가는데, 자동 테스트는 인증기를 손에 들 수 없다 —
    시크릿을 모르기 때문이다. 한 번 등록해 버리면 다음 실행에서는 아무도
    그 계정으로 로그인할 수 없다(시크릿이 응답에 한 번만 나온다).

    그래서 **시크릿을 환경이 정해 주는** 관리자를 하나 더 둔다. 세 값이 다
    있어야 만들고, 운영에서는 셋 다 비어 있어 아무것도 하지 않는다.

    부트스트랩 관리자에게 붙이지 않는 이유: 그 계정에 인증기가 생기면
    로그인마다 코드를 요구받는다. 개발·테스트가 쓰는 평범한 로그인 경로가
    통째로 바뀌므로 두 계정을 갈라 둔다.
    """
    email = os.getenv("SEED_MFA_ADMIN_EMAIL")
    password = os.getenv("SEED_MFA_ADMIN_PASSWORD")
    secret = os.getenv("SEED_MFA_ADMIN_TOTP_SECRET")
    if not (email and password and secret):
        return

    from ieum.core.crypto import SecretBox
    from ieum.modules.identity.models import MFACredential
    from ieum.modules.identity.service import MFA_SECRET_PURPOSE

    email = normalize_email(email)
    users = UserRepository(session)
    user = await users.get_by_email(email)
    if user is None:
        hasher = PasswordHashingService(
            memory_cost=settings.argon2_memory_cost,
            time_cost=settings.argon2_time_cost,
            parallelism=settings.argon2_parallelism,
        )
        user = User(
            email=email,
            display_name="Administrator (2FA)",
            status="active",
            password_hash=hasher.hash(password),
            locale=settings.default_locale,
        )
        users.add(user)
        await session.flush()
        log.info("seed.mfa_admin_created", email=email)

    from sqlalchemy import select

    from ieum.modules.org.models import RoleAssignment

    assigned = select(RoleAssignment).where(
        RoleAssignment.role_id == admin_role.id,
        RoleAssignment.principal_id == user.id,
        RoleAssignment.scope_kind == ScopeKind.GLOBAL.value,
    )
    if (await session.execute(assigned)).scalar_one_or_none() is None:
        RoleRepository(session).assign(
            role_id=admin_role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=user.id,
        )

    existing = select(MFACredential).where(
        MFACredential.user_id == user.id, MFACredential.kind == "totp"
    )
    if (await session.execute(existing)).scalars().first() is not None:
        return

    box = SecretBox(settings.secret_key.get_secret_value(), purpose=MFA_SECRET_PURPOSE)
    session.add(
        MFACredential(
            user_id=user.id,
            kind="totp",
            secret_enc=box.encrypt(secret.strip().upper()),
            label="seeded",
            # 확인까지 마친 상태로 넣는다. 확인 절차 자체는 서버 테스트가 본다.
            confirmed_at=utcnow(),
        )
    )
    log.info("seed.mfa_admin_totp_seeded", email=email)


async def run_seed() -> int:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=False)
    init_engine(settings)
    async with session_scope() as session:
        await seed(session, settings)
    log.info("seed.done")
    return 0
