"""imports 요청·응답 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ieum.modules.imports.mapping import Match, PersonMatch, Target
from ieum.modules.imports.service import Loaded, Preview


class MatchResponse(BaseModel):
    source: str
    target_id: str
    target_name: str
    #: `name`·`override`·`rank`·`unmatched`. 화면이 이 값으로 다르게 그린다 —
    #: **짐작한 것(`rank`)과 이름이 맞은 것을 같게 보여 주면 안 된다.**
    how: str

    @classmethod
    def of(cls, match: Match) -> MatchResponse:
        return cls(
            source=match.source,
            target_id=match.target_id,
            target_name=match.target_name,
            how=match.how,
        )


class PersonResponse(BaseModel):
    source_id: str
    name: str
    email: str
    user_id: str
    #: 빈 값이면 이었다. `no_email` 과 `unknown_email` 은 **고치는 방법이
    #: 다르다** — 앞은 소스에 메일이 없는 것이고 뒤는 우리 쪽에 계정이 없는 것이다.
    reason: str

    @classmethod
    def of(cls, person: PersonMatch) -> PersonResponse:
        return cls(
            source_id=person.source_id,
            name=person.name,
            email=person.email,
            user_id=person.user_id,
            reason=person.reason,
        )


class ChoiceResponse(BaseModel):
    id: str
    name: str
    qualifier: str

    @classmethod
    def of(cls, target: Target) -> ChoiceResponse:
        return cls(id=target.id, name=target.name, qualifier=target.qualifier)


class CountedResponse(BaseModel):
    issues: int
    comments: int
    people: int
    already_here: int


class PreviewResponse(BaseModel):
    source_kind: str
    source_url: str
    project_key: str
    project_name: str
    taken_at: str
    adapter: str
    counted: CountedResponse
    #: 이대로 실어도 되는가. 거짓이면 `blocking` 에 이유가 있다.
    can_load: bool
    blocking: list[str]

    types: list[MatchResponse]
    statuses: list[MatchResponse]
    priorities: list[MatchResponse]
    people: list[PersonResponse]

    dangling_parents: list[str]
    dangling_relations: list[str]
    unknown_relation_kinds: list[str]

    type_choices: list[ChoiceResponse]
    status_choices: list[ChoiceResponse]

    @classmethod
    def of(cls, preview: Preview) -> PreviewResponse:
        report = preview.report
        return cls(
            source_kind=preview.source_kind,
            source_url=preview.source_url,
            project_key=preview.project_key,
            project_name=preview.project_name,
            taken_at=preview.taken_at,
            adapter=preview.adapter,
            counted=CountedResponse(
                issues=preview.counted.issues,
                comments=preview.counted.comments,
                people=preview.counted.people,
                already_here=preview.counted.already_here,
            ),
            can_load=preview.can_load,
            blocking=report.blocking,
            types=[MatchResponse.of(m) for m in report.types],
            statuses=[MatchResponse.of(m) for m in report.statuses],
            priorities=[MatchResponse.of(m) for m in report.priorities],
            people=[PersonResponse.of(p) for p in report.people],
            dangling_parents=report.dangling_parents,
            dangling_relations=report.dangling_relations,
            unknown_relation_kinds=report.unknown_relation_kinds,
            type_choices=[ChoiceResponse.of(t) for t in preview.choices.types],
            status_choices=[ChoiceResponse.of(t) for t in preview.choices.statuses],
        )


class OverridesRequest(BaseModel):
    """사람이 지어 준 짝. 소스의 낱말 → 우리 id(우선순위는 `1`~`5`)."""

    types: dict[str, str] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    priorities: dict[str, str] = Field(default_factory=dict)


class LoadedResponse(BaseModel):
    issues_created: int
    #: 이미 있어서 건너뛴 것. **다시 돌리면 이 값이 전부여야 한다.**
    issues_skipped: int
    comments_created: int
    parents_linked: int
    relations_linked: int
    #: 못 옮긴 것. 화면이 이것을 접어 두면 안 된다 — 개수는 나중에도 셀 수
    #: 있지만 이 목록은 여기서 안 보면 아무 데도 안 남는다.
    unmoved: list[str]

    @classmethod
    def of(cls, loaded: Loaded) -> LoadedResponse:
        return cls(
            issues_created=loaded.issues_created,
            issues_skipped=loaded.issues_skipped,
            comments_created=loaded.comments_created,
            parents_linked=loaded.parents_linked,
            relations_linked=loaded.relations_linked,
            unmoved=loaded.unmoved,
        )


__all__ = [
    "ChoiceResponse",
    "CountedResponse",
    "LoadedResponse",
    "MatchResponse",
    "OverridesRequest",
    "PersonResponse",
    "PreviewResponse",
]
