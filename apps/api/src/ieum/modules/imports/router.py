"""이관 라우터.

묶음은 **요청 하나에 통째로** 올라온다. 위키 임포트와 같은 이유다(`wiki/
router.py`): 서버가 내용을 읽어야 하고 크기가 제한돼 있다. 스토리지를 거치면
올린 것과 읽는 것 사이에 상태가 하나 더 생기는데, 미리 보기는 그것을 남길
이유가 없다 — 아무것도 저장하지 않는 화면이다.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.exceptions import ValidationError
from ieum.migrate.archive import MAX_ARCHIVE_BYTES
from ieum.modules.imports.schemas import LoadedResponse, OverridesRequest, PreviewResponse
from ieum.modules.imports.service import ImportService

imports_router = APIRouter(prefix="/imports", tags=["imports"])


def _overrides(raw: str | None) -> OverridesRequest:
    """멀티파트에는 JSON 몸통을 같이 실을 수 없어 문자열 칸으로 받는다.

    비어 있으면 짝이 없는 것이다. **못 읽으면 조용히 무시하지 않는다** —
    사람이 지어 준 짝을 버리고 미리 보기를 보여 주면, 그 화면을 믿고 적재를
    누른다.
    """
    if not raw or not raw.strip():
        return OverridesRequest()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"짝 지정을 읽지 못했다: {exc.msg}", code="imports.bad_overrides"
        ) from None
    if not isinstance(parsed, dict):
        raise ValidationError("짝 지정은 객체여야 한다.", code="imports.bad_overrides")
    return OverridesRequest.model_validate(parsed)


@imports_router.post("/preview", response_model=PreviewResponse)
async def preview_archive(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    overrides: Annotated[str | None, Form()] = None,
) -> PreviewResponse:
    """묶음을 읽어 무엇이 들어올지 보여 준다. **아무것도 저장하지 않는다.**"""
    raw = await file.read()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "묶음이 너무 크다.",
            code="imports.archive_too_large",
            details={"max": MAX_ARCHIVE_BYTES},
        )
    chosen = _overrides(overrides)
    preview = await ImportService(session, permissions).preview(
        actor,
        project_id=project_id,
        data=raw,
        type_overrides=chosen.types,
        status_overrides=chosen.statuses,
        priority_overrides=chosen.priorities,
    )
    return PreviewResponse.of(preview)


@imports_router.post("/load", response_model=LoadedResponse)
async def load_archive(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    overrides: Annotated[str | None, Form()] = None,
) -> LoadedResponse:
    """묶음을 적재한다. **두 번 쳐도 안 늘어난다.**

    미리 보기와 같은 몸통을 받는다 — 화면이 보여 준 그대로를 실어야 하고,
    다른 것을 실으면 사람이 본 것과 들어간 것이 갈린다.
    """
    raw = await file.read()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "묶음이 너무 크다.",
            code="imports.archive_too_large",
            details={"max": MAX_ARCHIVE_BYTES},
        )
    chosen = _overrides(overrides)
    loaded = await ImportService(session, permissions).load(
        actor,
        project_id=project_id,
        data=raw,
        type_overrides=chosen.types,
        status_overrides=chosen.statuses,
        priority_overrides=chosen.priorities,
    )
    await session.commit()
    return LoadedResponse.of(loaded)


__all__ = ["imports_router"]
