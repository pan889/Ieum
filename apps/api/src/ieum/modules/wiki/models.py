"""wiki ORM 모델 (docs/architecture/data-model.md wiki 절).

본문 정본은 **마크다운 텍스트**다 (ADR-0008). 렌더 결과를 저장하지 않는다 —
저장하면 렌더러를 고칠 때마다 과거 문서가 굳어 버린다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Archivable, Entity

SPACE_KINDS = ("team", "personal", "kb")
PAGE_STATUSES = ("draft", "published")
#: 문서의 성격. 블로그 글은 **트리에 안 들어간다** — 날짜순으로 흐르는 글이라
#: 위치가 아니라 시간이 자리를 정한다. 나머지(버전·코멘트·검색·권한)는 같다.
PAGE_KINDS = ("page", "blog")
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
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="page", server_default=text("'page'")
    )
    #: 블로그 글이 흐르는 기준 시각. 트리 문서에는 없다.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 낙관적 잠금. 이슈와 같은 규약(If-Match)을 쓴다.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(status.in_(PAGE_STATUSES), name="page_status"),
        CheckConstraint(kind.in_(PAGE_KINDS), name="page_kind"),
        # 같은 부모 아래에서 slug 는 유일하다. 부모가 NULL 인 최상위도 마찬가지라
        # 부분 인덱스를 따로 둔다 — NULL 은 UNIQUE 에서 서로 다르게 취급된다.
        Index(
            "uq_page_parent_slug",
            "parent_id",
            "slug",
            unique=True,
            postgresql_where=parent_id.isnot(None),
        ),
        # 최상위 slug 는 성격별로 유일하다. `kind` 를 빼면 블로그 글 하나가
        # 같은 이름의 최상위 문서를 막는다 — 둘은 주소부터 다른데도.
        Index(
            "uq_page_space_root_slug",
            "space_id",
            "kind",
            "slug",
            unique=True,
            postgresql_where=parent_id.is_(None),
        ),
        # 블로그 목록은 최신순이다.
        Index("ix_page_space_blog", "space_id", "published_at"),
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


class PageDraft(Entity):
    """저장하지 않은 편집. 사람마다 문서마다 하나.

    자동 저장을 `page_version` 으로 하지 않는 이유는 이력이 오염되기
    때문이다. 30초마다 판이 하나씩 쌓이면 "무엇이 언제 바뀌었나" 를 볼 수
    없게 된다. 초안은 따로 두고, 게시할 때만 판을 만든다.

    브라우저 저장소에 두지 않는 이유는 기기를 옮기면 사라지기 때문이다.
    긴 문서를 쓰다 노트북을 닫은 사람이 사무실 PC 에서 이어 쓸 수 있어야 한다.
    """

    __tablename__ = "page_draft"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 초안을 뜨기 시작한 판. 그 사이 남이 고쳤으면 화면이 알려 준다.
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("page_id", "author_id", name="uq_page_draft_page_id_author_id"),
        Index("ix_page_draft_page_id", "page_id"),
    )


class PageCollab(Entity):
    """동시 편집의 **공유 초안** 하나. 문서마다 하나다 (B16).

    `page_draft` 와 나란히 두는 이유는 임자가 다르기 때문이다: 초안은
    사람마다 하나(`(page_id, author_id)` 유일)이고, 이건 문서마다 하나다.
    같이 편집하는 자리에서는 "내 초안" 이라는 것이 없다.

    `state` 는 **CRDT 문서 전체 상태**다(`Doc.get_update()`). 텍스트만 담지
    않는 이유: 텍스트는 CRDT 상태에서 뽑을 수 있지만 거꾸로는 못 한다. 텍스트만
    저장하고 다시 CRDT 로 올리면 모든 편집 이력이 한 번에 지워지고, 그 순간
    다른 창에 남아 있던 클라이언트의 편집은 **없던 일**이 된다.

    마크다운을 함께 저장하지 않는 것도 같은 판단이다 — 두 벌은 어긋난다.
    본문이 필요하면 상태에서 뽑는다(`collab.text_of`).

    Redis 는 **전달**만 한다(프로세스 사이 팬아웃). 여기가 durable 이다:
    Redis 가 비면 방은 이 행에서 다시 선다. 반대로 이 행이 없으면 방은 지금
    게시된 판의 본문에서 시작한다.
    """

    __tablename__ = "page_collab"

    #: 문서 하나에 방 하나. 두 개가 생기면 어느 쪽이 진짜인지 말할 수 없다.
    page_id: Mapped[UUID] = mapped_column(
        ForeignKey("page.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: 마지막으로 스냅샷을 남긴 사람. 통계가 아니라 **누가 편집 중이었나** 의
    #: 흔적이다 — 계정이 사라져도 스냅샷은 남아야 하므로 SET NULL 이다.
    saved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class PageTask(Entity):
    """본문의 태스크 한 줄 — **유도 표다** (B12).

    ## 여기에 쓰지 않는다

    정본은 본문의 마크다운이다(ADR-0008). 이 표는 검색 색인과 같은 성질이다:
    문서를 저장할 때 **본문에서 다시 만들어진다.** 그래서 이 표를 직접 고치면
    다음 저장에서 조용히 사라진다 — 고치는 곳은 본문이다.

    유도 표를 두는 이유는 집계뿐이다. "내 할 일" 은 문서 수백 개의 본문을
    가로질러야 하는데, 그걸 매번 파싱하면 화면 하나가 스페이스 전체를 읽는다.

    ## 담당자와 기한도 본문에서 온다

    `assignee_id` 는 그 줄의 첫 멘션이고 `due_date` 는 `due:YYYY-MM-DD` 다.
    별도 칸에 사람이 입력하게 두면 임포트·소스 편집·되돌리기에서 두 벌이
    어긋난다.
    """

    __tablename__ = "page_task"

    page_id: Mapped[UUID] = mapped_column(ForeignKey("page.id", ondelete="CASCADE"), nullable=False)
    #: 원문 줄 번호(0부터). **태스크의 신원이다** — 몇 번째냐로 세면 중첩
    #: 목록에서 화면과 서버가 어긋날 수 있고, 어긋난 채로 체크하면 다른 줄이
    #: 바뀐다.
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 그 줄의 첫 멘션. 계정이 사라져도 태스크는 남아야 하므로 SET NULL 이다.
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        # 한 줄에 태스크는 하나다. 둘 생기면 집계가 같은 것을 두 번 센다.
        UniqueConstraint("page_id", "line", name="uq_page_task_page_id_line"),
        # 집계가 "내 것 중 안 끝난 것" 을 기한순으로 훑는다.
        Index("ix_page_task_assignee_open", "assignee_id", "due_date"),
        Index("ix_page_task_page_id", "page_id"),
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
