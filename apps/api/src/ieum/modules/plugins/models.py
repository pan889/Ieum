"""plugins ORM 모델 (docs/architecture/data-model.md plugins 절)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Base, Entity
from ieum.modules.plugins.slots import KINDS


class App(Entity):
    """등록된 앱 하나.

    **토큰은 해시로만 둔다.** 낼 때 한 번 보여 주고 우리는 잊는다 — PAT 과
    같은 규칙이다(identity). 다시 볼 수 있게 두면 그 순간부터 이 표가
    비밀 창고가 되고, 목록을 읽을 수 있는 사람이 모든 앱을 사칭할 수 있다.

    **웹훅을 직접 갖지 않는다.** 서버 이벤트는 이미 notify 의 웹훅이
    서명·재시도·전송 로그까지 들고 있다(M2). 같은 것을 여기 또 만들면 세는
    규칙이 두 곳에 생긴다. 그래서 앱은 웹훅 하나를 **가리킨다**.
    """

    __tablename__ = "app"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: 주소·헤더에 실리는 짧은 이름.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 앱을 만든 곳의 주소(선택). 관리 화면이 "이게 무슨 앱인지" 를 보여 준다.
    homepage_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: 토큰 해시. 원문은 어디에도 없다.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: 토큰 앞 몇 글자. 어느 토큰인지 사람이 짚을 수 있게 남긴다.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    #: 이 앱이 받는 서버 이벤트를 실어 보내는 웹훅. 안 받으면 NULL 이다.
    #:
    #: **SET NULL 이다.** 웹훅이 사라져도 앱과 그 자리는 남아야 한다 —
    #: 이벤트를 안 받는 앱(패널만 쓰는 앱)이 정당한 형태다.
    webhook_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("webhook.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 토큰으로 마지막에 들어온 시각. "이 앱이 살아 있나" 의 근거다.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_app_enabled", "enabled", postgresql_where=text("enabled")),)


class AppSlot(Entity):
    """앱이 화면에서 차지한 자리 하나.

    **자리는 관리자가 정한다.** 앱이 스스로 자리를 넓힐 수 없다 — 자리를
    고르는 것과 그 안을 채우는 것은 다른 권한이고, 후자만 앱의 것이다.
    자기 자리를 옮길 수 있으면 앱 하나가 전이 버튼 옆으로 옮겨 앉을 수 있다.
    """

    __tablename__ = "app_slot"

    app_id: Mapped[UUID] = mapped_column(ForeignKey("app.id", ondelete="CASCADE"), nullable=False)
    #: `slots.SLOTS` 의 이름. 모르는 값은 서비스가 거절한다.
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    #: 링크 자리만 쓴다. 패널은 NULL 이다.
    url_template: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(kind.in_(KINDS), name="app_slot_kind"),
        # 한 앱이 같은 자리에 같은 이름을 두 번 놓지 않는다. 두 개가 나란히
        # 같은 글자로 보이면 사람은 어느 쪽을 눌러야 하는지 알 수 없다.
        UniqueConstraint("app_id", "slot", "label", name="uq_app_slot_label"),
        # 링크 자리는 주소가 있어야 하고, 패널은 없어야 한다. 이 줄이 없으면
        # 주소 없는 링크가 화면에 "아무 데도 안 가는 글자" 로 남는다.
        CheckConstraint(
            "(kind = 'link') = (url_template IS NOT NULL)",
            name="app_slot_url_matches_kind",
        ),
        Index("ix_app_slot_slot", "slot"),
        Index("ix_app_slot_app_id", "app_id"),
    )


class AppPanel(Base):
    """앱이 이슈 하나에 써 둔 글.

    **본문은 마크다운이다.** 우리 파이프라인으로 그리므로 원시 HTML 이
    죽는다(`html: false`). HTML 을 받는 길을 두지 않는 이유는 slots.py 머리에
    적어 두었다.

    `Entity` 가 아니라 `Base` 인 이유: 이 행의 정체는 (자리, 이슈) 쌍이고
    따로 id 를 둘 이유가 없다. 앱은 이슈 키로 쓰고, 같은 이슈에 두 번 쓰면
    덮어쓴다 — 상태를 보여 주는 칸이라 이력이 아니라 현재만 뜻이 있다.
    """

    __tablename__ = "app_panel"

    slot_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_slot.id", ondelete="CASCADE"), primary_key=True
    )
    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("ix_app_panel_issue", "issue_id"),)
