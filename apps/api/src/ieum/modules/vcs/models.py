"""vcs ORM 모델 (A22, M6).

## 저장소는 프로젝트를 **밝혀 적는다**

`project_ids` 가 비어 있을 수 없다. "비우면 전부" 로 두면 시크릿이 새는 날
그 하나로 설치 전체의 이슈에 글을 붙일 수 있고, 무엇보다 "이 저장소가 어느
팀의 코드인가" 를 아무도 안 적게 된다.

모노레포가 두 프로젝트를 담으면 둘을 함께 적는다. 등록하는 사람은 **그
프로젝트 전부에** 권한이 있어야 한다.

## 링크는 한 번만 생긴다

웹훅은 다시 온다(재전송, 재시도, 사람이 누른 "redeliver"). `(issue, repo,
kind, ref)` 에 유니크를 걸어 두면 같은 커밋이 두 줄로 쌓이지 않는다 —
애플리케이션에서만 막으면 두 전송이 동시에 올 때 둘 다 통과한다.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Entity

#: 붙일 수 있는 코드 호스트. 서명 확인 방식이 서로 다르다(`webhooks.py`).
PROVIDERS = ("github", "gitlab")

#: 이슈에 붙는 변경의 종류.
CHANGE_KINDS = ("commit", "pull_request")


class Repository(Entity):
    """연동한 저장소 하나."""

    __tablename__ = "vcs_repository"

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `org/repo`. 사람이 목록에서 알아보는 이름이다.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 저장소 웹 주소. 커밋 주소를 만들 때 쓴다 — 페이로드에 없는 호스트가
    #: 있어서(자체 호스팅 GitLab) 우리가 들고 있어야 한다.
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 서명 검증 키. AES-GCM 으로 감싸 저장한다 — 한 번만 보여 주고 다시
    #: 못 읽게 두면 저장소를 다시 등록해야 하고, 그러면 링크가 끊긴다.
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    #: 이 저장소의 커밋이 가리킬 수 있는 프로젝트. **비어 있을 수 없다.**
    project_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 마지막으로 무언가를 받은 때. **켰는데 아무것도 안 오는 것**이 이
    #: 연동의 흔한 고장이고, 그건 이 값이 비어 있는 것으로만 보인다.
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(provider.in_(PROVIDERS), name="vcs_repository_provider"),
        UniqueConstraint("provider", "name", name="uq_vcs_repository_provider_name"),
    )


class ChangeLink(Entity):
    """이슈에 붙은 커밋 또는 PR 하나."""

    __tablename__ = "vcs_change_link"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("vcs_repository.id", ondelete="CASCADE"), nullable=False
    )
    #: **이슈를 직접 가리킨다.** `issues` 의 테이블이지만 FK 는 DB 의 일이고,
    #: 모듈 경계는 코드에서 지킨다(이 모듈은 `issues.contracts` 만 부른다).
    #: FK 가 없으면 이슈를 지웠을 때 링크가 유령으로 남는다.
    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 커밋이면 SHA, PR 이면 번호. 문자열로 두는 이유는 둘을 한 열에 담기
    #: 때문이고, 유니크 제약이 이 값으로 재전송을 막는다.
    external_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    #: 저장소 쪽 표시 이름. 우리 계정과 잇지 않는다 — 메일이 같아도 같은
    #: 사람이라고 단정할 수 없고, 틀리면 남의 이름이 이슈 이력에 남는다.
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: `fixes ENG-12` 처럼 닫는다고 적혀 있었나. **상태는 옮기지 않는다**
    #: (`refs.py` 주석 참조) — 적혀 있었다는 사실만 남긴다.
    closing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(kind.in_(CHANGE_KINDS), name="vcs_change_link_kind"),
        # 웹훅은 다시 온다. **DB 가 막는다** — 애플리케이션에서만 막으면 두
        # 전송이 동시에 올 때 둘 다 통과한다.
        UniqueConstraint(
            "issue_id",
            "repository_id",
            "kind",
            "external_ref",
            name="uq_vcs_change_link_target",
        ),
        Index("ix_vcs_change_link_issue", "issue_id", "happened_at"),
    )
