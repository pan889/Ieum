"""wiki 도메인 이벤트.

페이로드에 ORM 객체를 넣지 않는다. 알림과 웹훅이 이걸 구독한다.

**초안은 이벤트를 내지 않는다.** 아직 아무에게도 보이지 않는 글이고, 30초마다
자동 저장되는 판까지 알리면 알림이 쓸모없어진다 (wiki-markdown.md 6.1절).

**스페이스 키와 경로를 페이로드에 싣는다.** notify 가 문서를 되짚어 읽으면
notify → wiki 방향 의존이 생기는데, 그건 의존 그래프에 없는 화살표다
(overview.md 모듈 의존 그래프). 링크(`/wiki/ENG/deploy`)를 만들려면 둘 다
필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from ieum.core.events import DomainEvent, events


@dataclass(frozen=True)
class _PageEvent(DomainEvent):
    """문서 이벤트의 공통 부분. 링크와 수신자 판정에 필요한 것들이다."""

    space_id: UUID
    space_key: str
    path: str
    title: str
    actor_id: UUID
    #: 본문에서 언급된 사용자. **이미 볼 권한을 확인한 사람만** 담는다
    #: — notify 는 문서 제한(restriction)을 못 보므로 여기서 걸러야 한다.
    mentioned_ids: list[UUID] = field(default_factory=list)


@events.register_event
@dataclass(frozen=True)
class PagePublished(_PageEvent):
    """처음 게시됐다. 초안에서 게시로 바뀐 것도 여기다."""

    event_type: ClassVar[str] = "wiki.page.published"
    aggregate_type: ClassVar[str] = "page"


@events.register_event
@dataclass(frozen=True)
class PageUpdated(_PageEvent):
    """게시된 문서에 새 판이 생겼다. 복원도 편집이므로 여기로 온다."""

    event_type: ClassVar[str] = "wiki.page.updated"
    aggregate_type: ClassVar[str] = "page"

    version_number: int = 0
    #: 편집자가 남긴 한 줄. 없으면 빈 문자열이다.
    message: str = ""


@events.register_event
@dataclass(frozen=True)
class PageCommented(_PageEvent):
    """문서에 코멘트가 달렸다. 인라인이든 문서 전체든 같다."""

    event_type: ClassVar[str] = "wiki.page.commented"
    aggregate_type: ClassVar[str] = "page"

    comment_id: UUID | None = None
    #: 인용에 붙은 코멘트인지. 목록에서 구분해 보여줄 수 있게 싣는다.
    inline: bool = False


__all__ = ["PageCommented", "PagePublished", "PageUpdated"]
