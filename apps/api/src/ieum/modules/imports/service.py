"""이관 묶음을 받는 쪽의 비즈니스 로직.

지금은 **미리 보기까지**다. 적재는 다음이다 — 이관은 되돌리기 번거로우니
"무엇이 들어오는가" 를 사람이 먼저 보고 정해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.permissions import PermissionService, Scope
from ieum.migrate.archive import RELATION_KINDS, Archive, ArchiveError, read_archive
from ieum.modules.identity import contracts as identity
from ieum.modules.imports import permissions as perms
from ieum.modules.imports.mapping import (
    NO_EMAIL,
    UNKNOWN_EMAIL,
    PersonMatch,
    Report,
    Target,
    match_names,
    match_priorities,
    used_vocabulary,
)
from ieum.modules.imports.models import TARGET_TYPES
from ieum.modules.imports.repository import ImportedObjectRepository
from ieum.modules.issues import contracts as issues
from ieum.modules.org import contracts as org


@dataclass(frozen=True, slots=True)
class Counted:
    """묶음에 무엇이 몇 개 들어 있는가."""

    issues: int = 0
    comments: int = 0
    people: int = 0
    #: 이 프로젝트에 **이미 들어와 있는** 이슈. 다시 돌려도 안 늘어나는 수다.
    already_here: int = 0


@dataclass(frozen=True, slots=True)
class Choices:
    """사람이 고를 수 있는 우리 쪽 후보. 화면이 짝짓기 상자를 그리는 데 쓴다."""

    types: list[Target] = field(default_factory=list)
    statuses: list[Target] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Preview:
    source_kind: str
    source_url: str
    project_key: str
    project_name: str
    taken_at: str
    adapter: str
    counted: Counted
    report: Report
    choices: Choices

    @property
    def can_load(self) -> bool:
        """이대로 실어도 되는가. 막는 것이 하나라도 있으면 아니다."""
        return not self.report.blocking


class ImportService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._imported = ImportedObjectRepository(session)

    async def preview(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        data: bytes,
        type_overrides: dict[str, str] | None = None,
        status_overrides: dict[str, str] | None = None,
        priority_overrides: dict[str, str] | None = None,
    ) -> Preview:
        """묶음을 읽어 **무엇이 들어오고 무엇이 안 들어오는지** 돌려준다.

        아무것도 저장하지 않는다. 같은 묶음을 짝을 바꿔 가며 몇 번이고
        돌려 볼 수 있어야 한다 — 그것이 이 화면의 쓸모다.
        """
        await self._perms.require(self._s, actor, perms.IMPORT_RUN, scope=Scope.project(project_id))
        project = await org.get_project(self._s, project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        if project.is_archived:
            raise ConflictError(
                "아카이브된 프로젝트로는 옮길 수 없다.", code="imports.project_archived"
            )

        archive = self._read(data)
        vocabulary = used_vocabulary(archive.issues)

        types = [
            # 하위 작업 전용 유형은 후보에서 뺀다. 부모 없는 이슈가 거기로
            # 들어가면 만들어지는 순간 규칙에 걸리고, 그 실패는 이관 도중에
            # 나타나서 절반만 들어온 상태를 남긴다.
            Target(str(row.id), row.name)
            for row in await issues.get_project_issue_types(self._s, project_id)
            if not row.is_subtask
        ]
        states = [
            Target(str(row.id), row.name, row.workflow_name)
            for row in await issues.get_project_states(self._s, project_id)
        ]

        report = Report(
            types=match_names(vocabulary.types, types, type_overrides),
            statuses=match_names(vocabulary.statuses, states, status_overrides),
            priorities=match_priorities(vocabulary.priorities, priority_overrides),
            people=await self._people(archive),
        )
        self._note_dangling(archive, report)

        known = await self._imported.known_source_ids(
            project_id=project_id,
            source_kind=archive.manifest.source.kind,
            target_type=TARGET_TYPES[0],
            source_ids=[issue.source_id for issue in archive.issues],
        )
        return Preview(
            source_kind=archive.manifest.source.kind,
            source_url=archive.manifest.source.base_url,
            project_key=archive.manifest.project.key,
            project_name=archive.manifest.project.name,
            taken_at=archive.manifest.taken_at,
            adapter=archive.manifest.adapter,
            counted=Counted(
                issues=len(archive.issues),
                comments=sum(len(issue.comments) for issue in archive.issues),
                people=len(archive.people),
                already_here=len(known),
            ),
            report=report,
            choices=Choices(types=types, statuses=states),
        )

    # ── 안쪽 ────────────────────────────────────────────────────

    def _read(self, data: bytes) -> Archive:
        """묶음의 오류를 **사용자 오류로** 바꾼다.

        `ArchiveError` 는 어느 파일 몇 번째 줄인지까지 적혀 있다. 그대로
        내보내야 관리자가 어댑터를 다시 돌릴지 파일을 고칠지 판단한다 —
        "묶음이 잘못됐다" 만 보여 주면 아무것도 못 한다.
        """
        try:
            return read_archive(data)
        except ArchiveError as exc:
            raise ValidationError(str(exc), code="imports.unreadable_archive") from None

    async def _people(self, archive: Archive) -> list[PersonMatch]:
        """메일로 잇는다. 이름으로 잇지 않는다 — 동명이인 하나에 남의 이슈가 된다."""
        out: list[PersonMatch] = []
        for person in archive.people:
            if not person.email:
                out.append(PersonMatch(person.source_id, person.name, reason=NO_EMAIL))
                continue
            user = await identity.get_user_by_email(self._s, person.email)
            if user is None:
                out.append(
                    PersonMatch(person.source_id, person.name, person.email, reason=UNKNOWN_EMAIL)
                )
                continue
            out.append(PersonMatch(person.source_id, person.name, person.email, str(user.id)))
        return out

    def _note_dangling(self, archive: Archive, report: Report) -> None:
        """묶음 **안에 없는 것**을 가리키는 부모·관계를 적어 둔다.

        어댑터가 프로젝트 하나만 뽑으므로, 다른 프로젝트의 이슈를 부모로 둔
        것이나 그쪽으로 걸린 관계는 상대가 여기 없다. 버리는 것 자체는 맞지만
        **몇 개를 버렸는지 말하지 않으면** 옮긴 뒤에 아무도 모른다.
        """
        present = {issue.source_id for issue in archive.issues}
        unknown_kinds: set[str] = set()
        for issue in archive.issues:
            if issue.parent and issue.parent not in present:
                report.dangling_parents.append(issue.source_id)
            for relation in issue.relations:
                if relation.kind not in RELATION_KINDS:
                    unknown_kinds.add(relation.kind)
                elif relation.target not in present:
                    report.dangling_relations.append(f"{issue.source_id} → {relation.target}")
        report.unknown_relation_kinds.extend(sorted(unknown_kinds))


__all__ = ["Choices", "Counted", "ImportService", "Preview"]
