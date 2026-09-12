"""이관 묶음을 받는 쪽의 비즈니스 로직.

**미리 보기가 적재의 앞단이다.** `load` 는 `preview` 를 먼저 부르고, 거기서
막는 것이 하나라도 나오면 아무것도 안 넣는다. 두 화면이 서로 다른 판단을
하는 일이 없어야 한다 — 사람은 미리 보기를 믿고 적재를 누른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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
    term,
    used_vocabulary,
)
from ieum.modules.imports.models import TARGET_TYPES, ImportedObject
from ieum.modules.imports.repository import ImportedObjectRepository
from ieum.modules.issues import contracts as issues
from ieum.modules.org import contracts as org

#: 묶음의 관계 어휘 → 우리 어휘. **이름이 하나 다르다.**
#:
#: 묶음은 `copied_to`, 우리는 `copied` 다. 같은 뜻인데 글자가 달라서, 표가
#: 없으면 이관 한가운데서 CHECK 제약에 걸린다 — 그 실패는 절반만 들어온
#: 상태를 남긴다.
RELATION_TO_LINK = {
    "relates": "relates",
    "duplicates": "duplicates",
    "blocks": "blocks",
    "precedes": "precedes",
    "copied_to": "copied",
}


def _moment(value: str) -> datetime | None:
    """ISO-8601 을 시각으로. 못 읽으면 `None` — 그 자리는 서버 시각이 된다."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Counted:
    """묶음에 무엇이 몇 개 들어 있는가."""

    issues: int = 0
    comments: int = 0
    people: int = 0
    #: 이 프로젝트에 **이미 들어와 있는** 이슈. 다시 돌려도 안 늘어나는 수다.
    already_here: int = 0


@dataclass(frozen=True, slots=True)
class Loaded:
    """적재가 무엇을 했는가.

    **`unmoved` 가 이 결과의 절반이다.** 들어온 개수는 나중에도 셀 수 있지만,
    안 들어온 것은 여기서 안 적으면 아무 데도 안 남는다.
    """

    issues_created: int = 0
    #: 이미 들어와 있어서 건너뛴 것. 다시 돌렸을 때 이 값이 전부여야 한다.
    issues_skipped: int = 0
    comments_created: int = 0
    parents_linked: int = 0
    relations_linked: int = 0
    unmoved: list[str] = field(default_factory=list)


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

        type_rows = [
            # 하위 작업 전용 유형은 후보에서 뺀다. 부모 없는 이슈가 거기로
            # 들어가면 만들어지는 순간 규칙에 걸리고, 그 실패는 이관 도중에
            # 나타나서 절반만 들어온 상태를 남긴다.
            row
            for row in await issues.get_project_issue_types(self._s, project_id)
            if not row.is_subtask
        ]
        state_rows = await issues.get_project_states(self._s, project_id)
        types = [Target(str(row.id), row.name) for row in type_rows]
        states = [Target(str(row.id), row.name, row.workflow_name) for row in state_rows]

        report = Report(
            types=match_names(vocabulary.types, types, type_overrides),
            statuses=match_names(vocabulary.statuses, states, status_overrides),
            priorities=match_priorities(vocabulary.priorities, priority_overrides),
            people=await self._people(archive),
        )
        self._note_dangling(archive, report)
        self._note_crossed_workflows(archive, report, type_rows, state_rows)

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

    async def load(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        data: bytes,
        type_overrides: dict[str, str] | None = None,
        status_overrides: dict[str, str] | None = None,
        priority_overrides: dict[str, str] | None = None,
    ) -> Loaded:
        """묶음을 실제로 적재한다.

        **두 번 돌려도 안 늘어난다.** `(프로젝트, 소스, 종류, 소스 id)` 로
        이미 옮긴 것을 기억하고 건너뛴다 — 이관은 한 번에 안 끝나고, 중간에
        멈춘 뒤 다시 돌리는 것이 정상이다.

        **막는 것이 하나라도 있으면 아무것도 안 넣는다.** 절반만 들어온
        상태가 가장 나쁘다 — 되돌리려면 무엇이 들어왔는지 세어야 하는데,
        그걸 세려고 이 표를 만든 것이다.
        """
        preview = await self.preview(
            actor,
            project_id=project_id,
            data=data,
            type_overrides=type_overrides,
            status_overrides=status_overrides,
            priority_overrides=priority_overrides,
        )
        if not preview.can_load:
            raise ConflictError(
                "짝을 못 지은 것이 있어 적재할 수 없다.",
                code="imports.unmapped_vocabulary",
                details={"blocking": preview.report.blocking},
            )

        archive = self._read(data)
        source = archive.manifest.source.kind
        report = preview.report
        types = {m.source: m.target_id for m in report.types if m.ok}
        statuses = {m.source: m.target_id for m in report.statuses if m.ok}
        priorities = {m.source: int(m.target_id) for m in report.priorities if m.ok}
        people = {p.source_id: UUID(p.user_id) for p in report.people if p.ok}
        unmoved: list[str] = []

        # ── 이슈 ────────────────────────────────────────────────
        known = await self._imported.resolve(
            project_id=project_id,
            source_kind=source,
            target_type=TARGET_TYPES[0],
            source_ids=[issue.source_id for issue in archive.issues],
        )
        created = 0
        for issue in archive.issues:
            if issue.source_id in known:
                continue
            issue_id = await issues.create_imported_issue(
                self._s,
                issues.ImportedIssueDraft(
                    project_id=project_id,
                    # **대체값을 두지 않는다.** 빈 칸과 못 이은 낱말은 위의
                    # `can_load` 가 이미 막았다. 여기에 "없으면 아무거나" 를
                    # 두면 그 가드가 헐거워지는 날 아무도 모른다 — 개수는
                    # 맞고 이슈만 엉뚱한 자리에 있다.
                    # **표준형으로 찾는다.** 어휘를 모을 때 `term` 으로 다듬어
                    # 담았으므로, 원본 문자열로 찾으면 앞뒤 공백 하나에
                    # `KeyError` 가 나고 그건 500 이다 — 미리 보기는 통과시켜
                    # 놓고 이슈를 절반쯤 만든 뒤에.
                    type_id=UUID(types[term(issue.type)]),
                    state_id=UUID(statuses[term(issue.status)]),
                    summary=issue.summary or "(제목 없음)",
                    description=issue.description,
                    reporter_id=people.get(issue.author),
                    assignee_id=people.get(issue.assignee),
                    priority=priorities.get(term(issue.priority), 3),
                    created_at=_moment(issue.created_at),
                    updated_at=_moment(issue.updated_at or issue.created_at),
                    start_date=_day(issue.start_date),
                    due_date=_day(issue.due_date),
                    progress=issue.done_ratio,
                    labels=tuple(issue.labels),
                ),
            )
            self._imported.add(
                ImportedObject(
                    project_id=project_id,
                    source_kind=source,
                    source_id=issue.source_id,
                    target_type=TARGET_TYPES[0],
                    target_id=issue_id,
                )
            )
            known[issue.source_id] = issue_id
            created += 1
            # **사람을 못 이었으면 그 이슈가 작성자를 잃는다.** 개수만 세면
            # 어느 이슈인지 못 찾으므로 이름을 남긴다.
            if issue.author and issue.author not in people:
                unmoved.append(f"작성자를 못 이었다: {issue.source_id}")
            if issue.assignee and issue.assignee not in people:
                unmoved.append(f"담당자를 못 이었다: {issue.source_id}")
        await self._s.flush()

        counted = await self._load_comments(archive, project_id, source, known, people)
        parents, relations = await self._load_links(archive, project_id, source, known, unmoved)

        unmoved.extend(f"부모가 묶음 밖이다: {row}" for row in report.dangling_parents)
        unmoved.extend(f"관계 상대가 묶음 밖이다: {row}" for row in report.dangling_relations)
        return Loaded(
            issues_created=created,
            issues_skipped=len(archive.issues) - created,
            comments_created=counted,
            parents_linked=parents,
            relations_linked=relations,
            unmoved=unmoved,
        )

    async def _load_comments(
        self,
        archive: Archive,
        project_id: UUID,
        source: str,
        issue_ids: dict[str, UUID],
        people: dict[str, UUID],
    ) -> int:
        """코멘트도 자기 열쇠를 가진다 — 이슈만 세면 중간에 멈춘 실행에서
        코멘트가 두 벌이 된다."""
        wanted = [c.source_id for issue in archive.issues for c in issue.comments]
        already = await self._imported.known_source_ids(
            project_id=project_id,
            source_kind=source,
            target_type=TARGET_TYPES[1],
            source_ids=wanted,
        )
        created = 0
        for issue in archive.issues:
            target = issue_ids.get(issue.source_id)
            if target is None:
                continue
            for comment in issue.comments:
                if comment.source_id in already:
                    continue
                comment_id = await issues.add_imported_comment(
                    self._s,
                    issue_id=target,
                    body=comment.body,
                    author_id=people.get(comment.author),
                    created_at=_moment(comment.created_at),
                )
                self._imported.add(
                    ImportedObject(
                        project_id=project_id,
                        source_kind=source,
                        source_id=comment.source_id,
                        target_type=TARGET_TYPES[1],
                        target_id=comment_id,
                    )
                )
                created += 1
        await self._s.flush()
        return created

    async def _load_links(
        self,
        archive: Archive,
        project_id: UUID,
        source: str,
        issue_ids: dict[str, UUID],
        unmoved: list[str],
    ) -> tuple[int, int]:
        """부모와 관계는 **이슈를 다 만든 뒤에** 잇는다.

        부모가 자식보다 뒤에 올 수 있다. 앞에서 이으려 하면 아직 없는 것을
        가리키고, 그때 건너뛰면 그 관계는 영영 안 생긴다.
        """
        known = frozenset(issues.link_kinds())
        parents = 0
        relations = 0
        for issue in archive.issues:
            here = issue_ids.get(issue.source_id)
            if here is None:
                continue
            parent = issue_ids.get(issue.parent) if issue.parent else None
            if parent is not None:
                await issues.set_imported_parent(self._s, issue_id=here, parent_id=parent)
                parents += 1
            for relation in issue.relations:
                kind = RELATION_TO_LINK.get(relation.kind)
                if kind is None or kind not in known:
                    unmoved.append(f"모르는 관계 종류: {relation.kind} ({issue.source_id})")
                    continue
                target = issue_ids.get(relation.target)
                if target is None:
                    continue
                if await issues.link_imported_issues(
                    self._s, from_issue_id=here, to_issue_id=target, kind=kind
                ):
                    relations += 1
        await self._s.flush()
        return parents, relations

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

    def _note_crossed_workflows(
        self,
        archive: Archive,
        report: Report,
        type_rows: list[issues.IssueTypeRef],
        state_rows: list[issues.StateRef],
    ) -> None:
        """고른 상태가 고른 종류의 워크플로우에 없으면 적어 둔다.

        **상태 목록은 프로젝트의 모든 워크플로우를 한 통에 담는다.** 워크플로우가
        둘 이상인 프로젝트에서는 이름만 보고 고른 상태가 다른 워크플로우의 것일
        수 있고, 그대로 실으면 이슈는 만들어지는데 그 상태에서 나갈 전이가
        하나도 없다 — 아무도 못 옮기는 이슈가 조용히 쌓인다. `IssueTypeRef` 가
        `workflow_id` 를 함께 내는 이유가 이 판단이다.

        **묶음에 실제로 나온 (종류, 상태) 짝만 본다.** 나오지 않은 조합까지
        따지면 워크플로우가 여럿인 프로젝트는 아무것도 못 옮긴다.
        """
        type_workflow = {str(row.id): row.workflow_id for row in type_rows}
        state_workflow = {str(row.id): row.workflow_id for row in state_rows}
        chosen_type = {m.source: m.target_id for m in report.types if m.ok}
        chosen_state = {m.source: m.target_id for m in report.statuses if m.ok}

        seen: set[tuple[str, str]] = set()
        for issue in archive.issues:
            pair = (term(issue.type), term(issue.status))
            if pair in seen:
                continue
            seen.add(pair)
            type_id = chosen_type.get(pair[0])
            state_id = chosen_state.get(pair[1])
            if type_id is None or state_id is None:
                # 짝을 못 지은 것은 이미 `blocking` 에 들어 있다.
                continue
            if type_workflow.get(type_id) != state_workflow.get(state_id):
                report.crossed_workflows.append(pair)

    def _note_dangling(self, archive: Archive, report: Report) -> None:
        """묶음 **안에 없는 것**을 가리키는 부모·관계를 적어 둔다.

        어댑터가 프로젝트 하나만 뽑으므로, 다른 프로젝트의 이슈를 부모로 둔
        것이나 그쪽으로 걸린 관계는 상대가 여기 없다. 버리는 것 자체는 맞지만
        **몇 개를 버렸는지 말하지 않으면** 옮긴 뒤에 아무도 모른다.
        """
        present = {issue.source_id for issue in archive.issues}
        unknown_kinds: set[str] = set()
        for issue in archive.issues:
            # 빈 칸은 짝지을 낱말이 없다. 우리가 골라 주면 그 이슈들이
            # 아무도 안 고른 자리로 들어가고 개수는 맞는다.
            if not issue.type.strip():
                report.blank_type.append(issue.source_id)
            if not issue.status.strip():
                report.blank_status.append(issue.source_id)
            if issue.parent and issue.parent not in present:
                report.dangling_parents.append(issue.source_id)
            for relation in issue.relations:
                if relation.kind not in RELATION_KINDS:
                    unknown_kinds.add(relation.kind)
                elif relation.target not in present:
                    report.dangling_relations.append(f"{issue.source_id} → {relation.target}")
        report.unknown_relation_kinds.extend(sorted(unknown_kinds))


__all__ = ["Choices", "Counted", "ImportService", "Loaded", "Preview"]
