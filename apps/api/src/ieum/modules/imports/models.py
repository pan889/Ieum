"""imports ORM 모델.

## 무엇을 이미 옮겼는지 기억한다

이관은 **한 번에 끝나지 않는다.** 소스가 살아 있는 채로 옮기기 시작하고,
중간에 멈추고, 며칠 뒤 다시 돌린다. 그때마다 같은 이슈가 새로 만들어지면
옮긴 쪽에서 세 벌이 쌓이고 어느 것이 진짜인지 아무도 모른다.

그래서 소스의 id 와 우리 id 를 짝지어 남긴다. 두 번째 실행은 **이미 있는
것을 건너뛴다.**

## 열쇠에 프로젝트가 들어간다

`(소스 종류, 소스 id)` 만으로는 부족하다 — Redmine 두 대에서 각각 1번 이슈를
옮길 수 있고, 같은 Redmine 을 두 프로젝트로 나눠 옮길 수도 있다. 프로젝트를
열쇠에 넣으면 둘 다 자연스럽게 갈린다: **한 프로젝트는 한 소스에서 온다.**

`base_url` 을 열쇠에 안 넣은 이유: 서버는 이사한다. 주소가 바뀌었다고 같은
이슈가 두 벌이 되면 안 된다.

## 유니크는 DB 에 건다

애플리케이션에서만 막으면 두 실행이 동시에 돌 때 둘 다 통과한다. 관리자가
"안 도는 것 같다" 며 두 번 누르는 것은 흔한 일이다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Entity

#: 무엇을 옮겼는가. 이슈와 코멘트가 각자 자기 열쇠를 가진다 — 이슈만 세면
#: 중간에 멈춘 실행에서 코멘트가 두 벌이 된다.
TARGET_TYPES = ("issue", "comment")


class ImportedObject(Entity):
    """소스의 것 하나가 우리 쪽 무엇이 되었는가."""

    __tablename__ = "imported_object"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_kind",
            "target_type",
            "source_id",
            name="uq_imported_object_identity",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    #: `redmine` 처럼. 같은 프로젝트에 두 소스를 섞어 넣는 일은 막지 않는다 —
    #: 막을 이유가 없고, 열쇠에 들어 있어 서로 안 부딪친다.
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 소스에서의 id. 숫자여도 글자로 싣는다 — 소스마다 모양이 다르다.
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 우리 쪽 id. **FK 를 안 건다** — 가리키는 표가 종류마다 다르고,
    #: 옮긴 이슈를 사람이 지웠을 때 이 기록까지 사라지면 다시 돌릴 때
    #: 지운 것이 되살아난다. 그건 지운 사람이 원한 일이 아니다.
    target_id: Mapped[UUID] = mapped_column(nullable=False)


__all__ = ["TARGET_TYPES", "ImportedObject"]
