"""모든 ORM 모델을 한곳에서 모은다.

Base.metadata 를 쓰는 쪽(alembic autogenerate, 테스트의 create_all)은 모델이
전부 import 돼 있어야 한다. 각자 import 목록을 관리하면 새 모듈을 추가할 때
한쪽이 빠지고, 그 결과가 "마이그레이션에서 조용히 누락"이라 알아채기 어렵다.
그래서 목록을 여기 한 벌만 둔다.
"""

from __future__ import annotations

from ieum.core.outbox import OutboxEvent
from ieum.db.base import Base
from ieum.modules.identity.models import (
    ApiToken,
    AuditLog,
    GroupMember,
    LoginAttempt,
    MFACredential,
    User,
    UserGroup,
    UserSession,
)
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueCategory,
    IssueComment,
    IssueFieldValue,
    IssueHistory,
    IssueLabel,
    IssueLink,
    IssueType,
    SavedFilter,
    SecurityLevel,
    Version,
    Workflow,
    WorkflowState,
    WorkflowTransition,
    Worklog,
)
from ieum.modules.org.models import (
    EntityLink,
    PermissionGrant,
    Project,
    Role,
    RoleAssignment,
    Workspace,
)

__all__ = [
    "ApiToken",
    "AuditLog",
    "Base",
    "EntityLink",
    "FieldDefinition",
    "GroupMember",
    "Issue",
    "IssueCategory",
    "IssueComment",
    "IssueFieldValue",
    "IssueHistory",
    "IssueLabel",
    "IssueLink",
    "IssueType",
    "LoginAttempt",
    "MFACredential",
    "OutboxEvent",
    "PermissionGrant",
    "Project",
    "Role",
    "RoleAssignment",
    "SavedFilter",
    "SecurityLevel",
    "User",
    "UserGroup",
    "UserSession",
    "Version",
    "Workflow",
    "WorkflowState",
    "WorkflowTransition",
    "Worklog",
    "Workspace",
]
