"""데모용 커스텀 필드 정의. `python -m ieum.cli seed-fields` 가 호출한다.

필드 정의를 만드는 관리 화면은 아직 없다 (M1 범위 밖). 그래서 종류별
위젯을 눈으로 확인하거나 E2E 로 돌리려면 정의를 넣어 줄 방법이 필요하다.
**운영 시드(`seed`)와 분리**한다 — 데모 데이터가 운영 설치에 섞이면
지우기 어렵다.

멱등이다. key 로 찾아 없을 때만 넣는다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 모델 레지스트리를 통째로 로드한다 (D-64). `FieldDefinition` 만 import 하면
# 그 FK 가 가리키는 `project` 가 매퍼에 없어서, **빈 DB 에 실제로 넣는 순간**
# NoReferencedTableError 로 죽는다. 이미 값이 있으면 flush 가 없어 안 터지므로
# 두 번째 실행부터는 멀쩡해 보인다.
import ieum.db.models  # noqa: F401
from ieum.config import get_settings
from ieum.core.logging import configure_logging, get_logger
from ieum.db.session import init_engine, session_scope
from ieum.modules.issues.models import FieldDefinition

log = get_logger(__name__)

#: (key, 이름, 종류, config). 아홉 종류를 한 번씩 쓴다.
DEMO_FIELDS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("cf_text", "Notes", "text", {"max_length": 200}),
    ("cf_number", "Story points", "number", {"min": 0, "max": 100}),
    ("cf_date", "Target date", "date", {}),
    ("cf_select", "Severity", "select", {"options": ["low", "medium", "high"]}),
    ("cf_multi", "Platforms", "multi_select", {"options": ["web", "ios", "android"]}),
    ("cf_bool", "Regression", "bool", {}),
    ("cf_url", "Spec link", "url", {}),
    ("cf_user", "Reviewer", "user", {}),
    ("cf_version", "Fix version", "version", {}),
)


async def seed_demo_fields(session: AsyncSession) -> int:
    """전역 커스텀 필드 정의를 넣는다. 이미 있으면 건드리지 않는다."""
    keys = [key for key, *_ in DEMO_FIELDS]
    existing = {
        row.key
        for row in (
            await session.execute(select(FieldDefinition).where(FieldDefinition.key.in_(keys)))
        ).scalars()
    }
    created = 0
    for position, (key, name, kind, config) in enumerate(DEMO_FIELDS):
        if key in existing:
            continue
        session.add(
            FieldDefinition(
                key=key,
                name=name,
                kind=kind,
                config=config,
                is_required=False,
                position=position,
            )
        )
        created += 1
    if created:
        await session.flush()
    log.info("seed.demo_fields", created=created, existing=len(existing))
    return created


async def run_seed_fields() -> int:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=False)
    init_engine(settings)
    async with session_scope() as session:
        await seed_demo_fields(session)
    return 0
