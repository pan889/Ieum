"""모든 ORM 모델을 한곳에서 모은다.

Base.metadata 를 쓰는 쪽(alembic autogenerate, 테스트의 create_all)은 모델이
전부 import 돼 있어야 한다. 각자 import 목록을 관리하면 새 모듈을 추가할 때
한쪽이 빠지고, 그 결과가 "마이그레이션에서 조용히 누락"이라 알아채기 어렵다.
그래서 목록을 여기 한 벌만 둔다.
"""

from __future__ import annotations

from ieum.core.attachments import Attachment
from ieum.core.heartbeat import Heartbeat
from ieum.core.outbox import OutboxEvent
from ieum.db.base import Base
from ieum.modules.desk.models import (
    Approval,
    ApprovalVote,
    Asset,
    AssetLink,
    AssetType,
    CustomerMembership,
    CustomerOrganization,
    Portal,
    RequestType,
    TicketExt,
)
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
    Board,
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
from ieum.modules.notify.models import (
    Notification,
    NotificationPreference,
    Watch,
    Webhook,
    WebhookDelivery,
)
from ieum.modules.org.models import (
    EntityLink,
    PermissionGrant,
    Project,
    Role,
    RoleAssignment,
    Workspace,
)
from ieum.modules.plugins.models import App, AppPanel, AppSlot
from ieum.modules.search.models import SearchDocument, SearchMirrorQueue
from ieum.modules.vcs.models import ChangeLink, Repository
from ieum.modules.wiki.models import (
    Page,
    PageComment,
    PageDraft,
    PageLabel,
    PageRestriction,
    PageTemplate,
    PageVersion,
    Space,
)

__all__ = [
    "ApiToken",
    "App",
    "AppPanel",
    "AppSlot",
    "Approval",
    "ApprovalVote",
    "Asset",
    "AssetLink",
    "AssetType",
    "Attachment",
    "AuditLog",
    "Base",
    "Board",
    "ChangeLink",
    "CustomerMembership",
    "CustomerOrganization",
    "EntityLink",
    "FieldDefinition",
    "GroupMember",
    "Heartbeat",
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
    "Notification",
    "NotificationPreference",
    "OutboxEvent",
    "Page",
    "PageComment",
    "PageDraft",
    "PageLabel",
    "PageRestriction",
    "PageTemplate",
    "PageVersion",
    "PermissionGrant",
    "Portal",
    "Project",
    "Repository",
    "RequestType",
    "Role",
    "RoleAssignment",
    "SavedFilter",
    "SearchDocument",
    "SearchMirrorQueue",
    "SecurityLevel",
    "Space",
    "TicketExt",
    "User",
    "UserGroup",
    "UserSession",
    "Version",
    "Watch",
    "Webhook",
    "WebhookDelivery",
    "Workflow",
    "WorkflowState",
    "WorkflowTransition",
    "Worklog",
    "Workspace",
]
