"""통합 검색 색인 (ADR-0005).

원본 테이블을 직접 뒤지지 않고 **평평한 색인 한 장**을 둔다. 이유가 셋이다.

1. 이슈와 문서는 모양이 달라서, 원본을 UNION 하면 질의가 두 벌이 되고
   랭킹을 한 자로 잴 수 없다.
2. 문서 본문은 `page_version` 에 판마다 쌓인다. 거기에 색인을 걸면 지난
   판까지 전부 색인되고 검색 결과에 옛 판이 섞인다.
3. 권한 필터를 색인 안에서 끝낼 수 있다. 결과를 가져와서 거르면 페이지
   크기가 맞지 않고, 무엇보다 한 군데라도 빠뜨리면 그게 유출이다.

색인은 원본과 **같은 트랜잭션**에서 갱신한다 (ADR-0005: "트랜잭션과 색인의
일관성 확보 쉬움"). 워커로 미루면 방금 만든 이슈가 검색에 없다.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ieum.core.time import utcnow
from ieum.db.base import Base, Entity

#: 색인에 들어가는 것들. 데스크 티켓은 이슈로 들어온다(티켓 = 이슈).
DOCUMENT_KINDS = ("issue", "page")

#: 스코프 종류. core.ScopeKind 와 이름을 맞춘다.
SCOPE_KINDS = ("project", "space")


class SearchDocument(Entity):
    __tablename__ = "search_document"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 원본 행의 id. 지울 때·되색인할 때 이걸로 찾는다.
    entity_id: Mapped[UUID] = mapped_column(nullable=False)

    #: 스코프 권한 필터용. 액터가 이 스코프에서 볼 권한이 있어야 결과에 든다.
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[UUID] = mapped_column(nullable=False)

    #: 사람이 읽는 식별자. 이슈 키(`ENG-1`)나 문서 경로(`ENG/deploy`).
    ref: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 평문. 마크다운 기호와 링크 URI 는 빼고 넣는다.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: 객체 수준 제한을 통과할 수 있는 주체들. NULL 이면 제한 없음.
    #: 스코프만으로 거르면 보안 레벨 이슈와 제한된 문서가 새어 나간다.
    #: 배열 교집합(`&&`)으로 거르려면 postgresql 방언의 ARRAY 여야 한다.
    #: 일반 sa.ARRAY 에는 `overlap` 비교자가 없다.
    restricted_to: Mapped[list[UUID] | None] = mapped_column(ARRAY(Uuid), nullable=True)

    #: 정렬용. 원본의 수정 시각을 그대로 복사한다.
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('issue', 'page')", name="search_document_kind"),
        CheckConstraint("scope_kind IN ('project', 'space')", name="search_document_scope_kind"),
        UniqueConstraint("kind", "entity_id", name="uq_search_document_kind_entity_id"),
        # 한국어 전문검색. 제목과 본문을 한 인덱스에 넣어야 `&@~` 가
        # 둘을 한 번에 훑고 `pgroonga_score` 도 값이 나온다. 모델에
        # 적어 두면 create_all 로 만드는 테스트 DB 에도 생긴다.
        Index("ix_search_document_fts", "title", "body", postgresql_using="pgroonga"),
        Index("ix_search_document_scope", "scope_kind", "scope_id"),
        Index("ix_search_document_updated", "source_updated_at"),
    )


class SearchMirrorQueue(Base):
    """**아직 OpenSearch 로 못 보낸 색인 키.** 미러를 쓸 때만 채워진다 (ADR-0015).

    ## 왜 아웃박스가 아닌가

    아웃박스는 도메인 이벤트의 통로다. 여기 태우면 등록된 이벤트 타입이 되고,
    그러면 웹훅 설정 화면에 `search.document.touched` 가 뜬다 — 아무도 그걸
    받고 싶지 않다. 이건 밖으로 알릴 일이 아니라 안쪽 배선이다.

    그리고 **뭉쳐진다.** 키가 PK 라서 같은 문서를 1분에 스무 번 고쳐도 줄은
    하나다. 아웃박스에 태우면 스무 줄이 되고 스무 번 보낸다. 미러가 필요한
    것은 "이 문서의 지금 상태" 뿐이므로, 뭉치는 것이 손실이 아니다.

    ## 값이 없다

    무엇이 바뀌었는지 적지 않는다. 미러 작업이 `search_document` 를 **다시
    읽어서** 지금 상태를 보내고, 행이 없으면 OpenSearch 에서 지운다. 그래서
    순서를 안 따지고, 두 번 처리해도 같고, 놓친 것을 나중에 처리해도 맞다.
    """

    __tablename__ = "search_mirror_queue"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(primary_key=True)
    #: 언제 들어왔나. 오래된 것부터 보내고, 밀린 나이를 경보에 쓴다.
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_search_mirror_queue_queued", "queued_at"),)


__all__ = ["DOCUMENT_KINDS", "SCOPE_KINDS", "SearchDocument", "SearchMirrorQueue"]
