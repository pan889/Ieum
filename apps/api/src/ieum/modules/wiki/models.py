"""wiki ORM 모델 (docs/architecture/data-model.md wiki 절).

본문 정본은 **마크다운 텍스트**다 (ADR-0008). 렌더 결과를 저장하지 않는다 —
저장하면 렌더러를 고칠 때마다 과거 문서가 굳어 버린다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Archivable, Entity

SPACE_KINDS = ("team", "personal", "kb")
PAGE_STATUSES = ("draft", "published")
RESTRICTION_MODES = ("view", "edit")
ANCHOR_STATUSES = ("ok", "orphaned")

#: 트리 깊이 상한. 무한 중첩은 경로 질의와 화면 양쪽을 못 쓰게 만든다.
MAX_DEPTH = 10


class Space(Entity, Archivable):
    __tablename__ = "space"

    #: URL 과 `page:KEY/slug` 링크에 쓰는 짧은 식별자.
    key: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="team")
    #: 스페이스를 열었을 때 처음 보이는 문서. 없으면 트리만 보여 준다.
    #: FK 는 마이그레이션에서 따로 붙인다 — space 와 page 가 서로를 가리켜
    #: autogenerate 가 순서를 못 정한다. 홈 문서가 지워지면 NULL 이 된다.
    home_page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("page.id", ondelete="SET NULL", use_alter=True), nullable=True
    )

    __table_args__ = (CheckConstraint(kind.in_(SPACE_KINDS), name="space_kind"),)


class Page(Entity, Archivable):
    """문서 한 편.

    본문은 여기 없다 — `page_version` 이 가진다. 페이지 행은 정체성(위치·제목·
    상태)만 들고, 내용은 버전이 소유한다. 그래야 이력이 1급이 된다.
    """

    __tablename__ = "page"

    space_id: Mapped[UUID] = mapped_column(
        ForeignKey("space.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("page.id", ondelete="CASCADE"), nullable=True
    )
    #: 조상 slug 를 `/` 로 이은 경로. 하위 트리 질의를 인덱스 하나로 끝낸다.
    #: ltree 대신 텍스트를 쓴다 — slug 에 하이픈이 들어가면 ltree 라벨 규칙을
    #: 어기고, 그걸 피하려고 slug 를 제약하면 URL 이 나빠진다.
    path: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 지금 게시된 버전. 초안만 있는 새 문서는 NULL 이다.
    #: FK 를 걸지 않는다 — page 와 page_version 이 서로를 가리키면 페이지를
    #: 지울 때 순환 CASCADE 순서를 Postgres 에 맡기게 된다. 버전은 불변이라
    #: 페이지가 사라질 때 말고는 지워지지 않으므로 매달릴 일이 없다.
    current_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 낙관적 잠금. 이슈와 같은 규약(If-Match)을 쓴다.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(status.in_(PAGE_STATUSES), name="page_status"),
        # 같은 부모 아래에서 slug 는 유일하다. 부모가 NULL 인 최상위도 마찬가지라
        # 부분 인덱스를 따로 둔다 — NULL 은 UNIQUE 에서 서로 다르게 취급된다.
        Index(
            "uq_page_parent_slug",
            "parent_id",
            "slug",
            unique=True,
            postgresql_where=parent_id.isnot(None),
        ),
        Index(
            "uq_page_space_root_slug",
            "space_id",
            "slug",
            unique=True,
            postgresql_where=parent_id.is_(None),
        ),
        Index("ix_page_space_id", "space_id"),
        Index("ix_page_parent_id", "parent_id"),
        # 하위 트리 조회는 `path LIKE 'a/b/%'` 다. 접두사 검색이라 인덱스를 탄다.
        Index("ix_page_space_path", "space_id", "path"),
    )


class PageVersion(Entity):
    """페이지 본문 한 판. 불변이다 — 고치면 새 판을 만든다."""

    __tablename__ = "page_version"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    #: 1 부터. 페이지 안에서만 유일하다.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 마크다운 정본. 정규화를 거친 결과만 들어온다.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    front_matter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 변경 요약. 커밋 메시지에 해당한다.
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("page_id", "number", name="uq_page_version_page_id_number"),
        Index("ix_page_version_page_id", "page_id"),
    )


class PageLabel(Entity):
    __tablename__ = "page_label"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint("page_id", "label", name="uq_page_label_page_id_label"),
        Index("ix_page_label_label", "label"),
    )


class PageRestriction(Entity):
    """문서 단위 열람·편집 제한.

    스페이스 권한을 **통과한 뒤에** 한 번 더 거른다. 이슈 보안 레벨과 같은
    구조다 (auth.md 5절) — 스코프 권한이 먼저고, 객체 제한이 나중이다.
    """

    __tablename__ = "page_restriction"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    principal_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    principal_id: Mapped[UUID] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(mode.in_(RESTRICTION_MODES), name="page_restriction_mode"),
        UniqueConstraint(
            "page_id",
            "mode",
            "principal_kind",
            "principal_id",
            name="uq_page_restriction_target",
        ),
        Index("ix_page_restriction_page_id", "page_id"),
    )


class PageComment(Entity):
    """문서 코멘트. `anchor` 가 있으면 인라인 코멘트다.

    마크다운에는 노드 id 가 없어서 텍스트 인용으로 위치를 잡는다
    (wiki-markdown.md 6절). 편집으로 인용문이 사라지면 고아가 되는데,
    **조용히 지우지 않고** 고아로 표시해 원문과 함께 남긴다.
    """

    __tablename__ = "page_comment"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: {exact, prefix, suffix, occurrence, version_number}. NULL 이면 페이지 코멘트.
    anchor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    anchor_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("page_comment.id", ondelete="CASCADE"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(anchor_status.in_(ANCHOR_STATUSES), name="page_comment_anchor_status"),
        Index("ix_page_comment_page_id", "page_id"),
        Index("ix_page_comment_parent_id", "parent_id"),
    )


class PageTemplate(Entity):
    __tablename__ = "page_template"

    #: NULL 이면 전역 템플릿. 지정되면 그 스페이스 전용.
    space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("space.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("ix_page_template_space_id", "space_id"),)
