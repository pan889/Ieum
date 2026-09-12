"""SCIM 2.0 의 값 다루기 — 순수 함수만 (RFC 7643·7644).

행을 쓰는 것은 `scim_service.py` 이고, 여기는 **바깥 규약을 우리 값으로
옮기는 자리**다. 층을 나누는 이유는 SCIM 이 우리가 정한 모양이 아니기
때문이다: 필터 문법도, PATCH 연산도, 오류 봉투도 IdP 가 기대하는 대로
맞춰야 하고, 그걸 손으로 적어 시험할 수 있어야 한다.

## 무엇을 받고 무엇을 거절하는가

SCIM 필터는 완전한 문법을 갖는다(`and`·`or`·`pr`·`co`·`sw`·괄호…). 전부
구현하지 않는다. IdP 가 실제로 보내는 것은 **`userName eq "..."`** 와
**`displayName eq "..."`** 뿐이고(생성 전 중복 확인), 나머지는 우리 목록
API 가 하는 일이다.

**모르는 필터는 거절한다.** 조용히 무시하면 IdP 는 "없다" 는 답을 받고
계정을 다시 만든다 — 같은 사람이 둘이 된다. 거절하면 IdP 의 로그에 남고,
그건 고칠 수 있는 실패다.

PATCH 도 같다. Entra 는 `replace active`, Okta 는 `add/remove members` 를
보낸다. 그 밖의 경로는 `invalidPath` 로 거절한다 — 무시하면 "비활성화했는데
계정이 살아 있다" 가 조용히 성립한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

#: 한 번에 내주는 최대 개수. IdP 는 `count` 를 크게 부르기도 한다.
MAX_COUNT = 200
DEFAULT_COUNT = 100


class ScimError(Exception):
    """SCIM 봉투로 나갈 오류.

    우리 오류 형식(`{"error": {...}}`)을 쓰지 않는다. IdP 는 SCIM 봉투를
    기대하고, 다른 모양이 오면 대개 "알 수 없는 실패" 로 접고 재시도한다 —
    무엇이 틀렸는지는 어디에도 안 남는다.
    """

    def __init__(self, status: int, detail: str, *, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"schemas": [ERROR_SCHEMA], "status": str(self.status)}
        if self.scim_type:
            out["scimType"] = self.scim_type
        out["detail"] = self.detail
        return out


@dataclass(frozen=True, slots=True)
class Filter:
    """우리가 받는 유일한 필터 모양: `<attribute> eq "<value>"`."""

    attribute: str
    value: str


#: `userName eq "a@b.c"`. 값의 따옴표 안에서 `\"` 를 쓸 수 있다.
_EQ = re.compile(r'^\s*(?P<attr>[A-Za-z][\w.]*)\s+eq\s+"(?P<value>(?:[^"\\]|\\.)*)"\s*$', re.I)

#: 필터를 걸 수 있는 속성. 자원 종류마다 다르다.
USER_FILTERABLE = ("username", "externalid")
GROUP_FILTERABLE = ("displayname",)


def parse_filter(raw: str | None, *, allowed: tuple[str, ...]) -> Filter | None:
    """필터 하나. 없으면 `None`, 못 다루는 것이면 거절."""
    if raw is None or raw.strip() == "":
        return None
    found = _EQ.match(raw)
    if found is None:
        raise ScimError(
            400,
            f'이 서버는 `attribute eq "value"` 만 받는다: {raw!r}',
            scim_type="invalidFilter",
        )
    attribute = found.group("attr").lower()
    if attribute not in allowed:
        raise ScimError(
            400,
            f"{found.group('attr')} 로는 거를 수 없다. 가능: {', '.join(allowed)}",
            scim_type="invalidFilter",
        )
    # `\"` 를 되돌린다. 정규식이 이미 짝을 맞춰 두었다.
    return Filter(attribute=attribute, value=found.group("value").replace('\\"', '"'))


def page_of(start_index: int | None, count: int | None) -> tuple[int, int]:
    """SCIM 의 페이지 지정을 offset·limit 으로.

    **`startIndex` 는 1부터다.** 0 이나 음수를 그대로 빼면 offset 이 음수가
    되고, 그건 DB 가 거절하거나(다행) 조용히 다른 페이지를 준다.
    """
    start = 1 if start_index is None or start_index < 1 else start_index
    size = DEFAULT_COUNT if count is None else count
    if size < 0:
        size = 0
    return start - 1, min(size, MAX_COUNT)


@dataclass(frozen=True, slots=True)
class PatchOp:
    """PATCH 연산 하나. 우리가 알아듣는 것으로 이미 좁혀졌다."""

    op: str
    path: str
    value: Any = None
    #: `members` 연산이 지목한 id 들. 다른 경로에서는 빈 목록.
    member_ids: list[str] = field(default_factory=list)


#: 사용자에게 허용하는 PATCH 경로. 소문자로 비교한다.
USER_PATHS = ("active", "username", "displayname", "name.givenname", "name.familyname")
GROUP_PATHS = ("members", "displayname")


def parse_patch(body: Any, *, allowed: tuple[str, ...]) -> list[PatchOp]:
    """PATCH 본문을 우리가 아는 연산 목록으로.

    **경로 없는 연산도 받는다.** Entra 는 `{"op":"replace","value":{"active":
    false}}` 처럼 경로를 빼고 보낸다 — 스펙이 허용하는 모양이고, 안 받으면
    비활성화가 통째로 안 된다.
    """
    if not isinstance(body, dict):
        raise ScimError(400, "PATCH 본문이 객체가 아니다", scim_type="invalidSyntax")
    schemas = body.get("schemas")
    if not isinstance(schemas, list) or PATCH_SCHEMA not in schemas:
        raise ScimError(400, f"schemas 에 {PATCH_SCHEMA} 가 없다", scim_type="invalidSyntax")
    operations = body.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise ScimError(400, "Operations 가 비어 있다", scim_type="invalidSyntax")

    out: list[PatchOp] = []
    for raw in operations:
        if not isinstance(raw, dict):
            raise ScimError(400, "연산이 객체가 아니다", scim_type="invalidSyntax")
        op = str(raw.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise ScimError(400, f"모르는 연산: {op!r}", scim_type="invalidSyntax")
        path = raw.get("path")
        if path is None:
            out.extend(_spread(op, raw.get("value"), allowed=allowed))
            continue
        out.append(_one(op, str(path), raw.get("value"), allowed=allowed))
    return out


def _spread(op: str, value: Any, *, allowed: tuple[str, ...]) -> list[PatchOp]:
    """경로 없는 연산을 속성마다 하나씩으로 편다."""
    if not isinstance(value, dict):
        raise ScimError(400, "경로 없는 연산의 value 는 객체여야 한다", scim_type="invalidSyntax")
    return [_one(op, key, item, allowed=allowed) for key, item in value.items()]


def _one(op: str, path: str, value: Any, *, allowed: tuple[str, ...]) -> PatchOp:
    # `members[value eq "x"]` 같은 값 필터가 붙어 온다. 경로 이름만 떼어
    # 보고, 필터 자체는 `remove` 의 대상 id 로 읽는다.
    name, _, inner = path.partition("[")
    key = name.strip().lower()
    if key not in allowed:
        raise ScimError(400, f"이 서버가 안 다루는 경로: {path}", scim_type="invalidPath")

    if key != "members":
        return PatchOp(op=op, path=key, value=value)

    ids = _member_ids(value)
    if inner:
        found = _EQ.match(inner.rstrip("]"))
        # `members[value eq "id"]` 만 받는다. 다른 필터는 무엇을 지우려는
        # 것인지 알 수 없고, 짐작해서 지우면 남의 멤버십이 사라진다.
        if found is None or found.group("attr").lower() != "value":
            raise ScimError(400, f"이 서버가 안 다루는 경로: {path}", scim_type="invalidPath")
        ids.append(found.group("value"))
    if not ids and op != "replace":
        raise ScimError(400, "members 연산에 대상이 없다", scim_type="invalidValue")
    return PatchOp(op=op, path=key, value=value, member_ids=ids)


def _member_ids(value: Any) -> list[str]:
    """`value` 가 담고 있는 멤버 id 들. 모양이 여러 가지다."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            found = item.get("value")
            if found is not None:
                out.append(str(found))
        elif isinstance(item, str):
            out.append(item)
    return out


def user_resource(
    *,
    user_id: str,
    user_name: str,
    display_name: str,
    active: bool,
    external_id: str | None,
    created_at: str,
    updated_at: str,
    location: str,
    group_refs: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """사용자 하나를 SCIM 자원으로.

    **비밀번호도 세션도 담지 않는다.** SCIM 응답은 IdP 의 로그에 남는다.
    """
    out: dict[str, Any] = {
        "schemas": [USER_SCHEMA],
        "id": user_id,
        "userName": user_name,
        "displayName": display_name,
        "name": {"formatted": display_name},
        "emails": [{"value": user_name, "primary": True}],
        "active": active,
        "meta": {
            "resourceType": "User",
            "created": created_at,
            "lastModified": updated_at,
            "location": location,
        },
    }
    if external_id is not None:
        out["externalId"] = external_id
    if group_refs:
        out["groups"] = [{"value": gid, "display": name} for gid, name in group_refs]
    return out


def group_resource(
    *,
    group_id: str,
    display_name: str,
    created_at: str,
    updated_at: str,
    location: str,
    members: list[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "schemas": [GROUP_SCHEMA],
        "id": group_id,
        "displayName": display_name,
        "members": [{"value": uid, "display": name} for uid, name in members],
        "meta": {
            "resourceType": "Group",
            "created": created_at,
            "lastModified": updated_at,
            "location": location,
        },
    }


def list_response(
    resources: list[dict[str, Any]], *, total: int, start_index: int
) -> dict[str, Any]:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def service_provider_config(location: str) -> dict[str, Any]:
    """무엇을 지원하는지 **정직하게** 적는다.

    Entra 는 이걸 읽고 무엇을 보낼지 정한다. `filter.supported` 를 참으로
    적어 두고 안 다루는 필터를 거절하면, IdP 는 자기가 맞게 부른 요청이
    실패하는 것을 본다.
    """
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        # **IdP 관리자가 화면에서 눌러 보는 링크다.** 비공개 저장소를 적으면
        # 그 사람에게는 404 다.
        "documentationUri": "https://github.com/pan889/Ieum",
        "patch": {"supported": True},
        # 여러 요청을 한 번에 보내는 것. 안 받는다 — 부분 실패의 의미를
        # 정의해야 하고, 그 답은 언제나 "절반만 반영됐다" 가 된다.
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_COUNT},
        # 우리는 비밀번호를 SCIM 으로 받지 않는다. IdP 가 인증을 갖는다.
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "프로비저닝 토큰. IdP 설정 화면에서 한 번만 보여 준다.",
                "primary": True,
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig", "location": location},
    }
