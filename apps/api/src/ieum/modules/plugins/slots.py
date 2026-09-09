"""확장 지점의 어휘와 그 값 검사. **순수 함수만 있다.**

여기가 이 모듈의 심장이다. 앱이 화면에 놓을 수 있는 자리와 값의 모양을
한 곳에서 정하고, 시험이 그것을 붙잡는다.

## 왜 자리를 고정 어휘로 두는가

앱이 자리 이름을 지어 낼 수 있으면 화면이 그것을 그릴 수 없다 — 우리가
모르는 이름은 아무 데도 안 나오고, 등록한 사람은 "왜 안 보이지" 를 알 방법이
없다. 조용히 아무 일도 안 하는 설정이 제일 나쁘다 (ux-principles).

그래서 **모르는 자리는 거절한다.** 자리를 늘리는 것은 화면을 함께 고치는
일이고, 그때 이 표에 한 줄을 더한다.

## 왜 코드를 안 받는가

앱은 **글을 보내고, 우리가 그린다.** 원격 스크립트나 iframe 을 받지 않는다:

- 남의 자바스크립트를 우리 페이지에 넣으면 그쪽이 세션과 DOM 을 함께 갖는다.
  설계로 만든 XSS 다.
- iframe 은 브라우저가 바깥 호스트에 직접 붙게 만든다. 데이터 주권(vision.md)
  과 어긋나고, 보안 경계가 그쪽 설정으로 넘어간다 — 헤드리스 브라우저를
  안 둔 것과 같은 판단이다(ADR-0011).

패널 본문은 마크다운이고, 우리 파이프라인으로 그린다. 그 방언은 원시 HTML 을
끄고(`html: false`) 링크 스킴을 화이트리스트로 막는다 — 앱이 보낸 글에
`<script>` 가 있어도 글자로 남는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ieum.core.exceptions import ValidationError

#: 링크가 쓸 수 있는 스킴. **`https` 뿐이다.**
#:
#: `http` 를 빼는 이유: 앱 링크는 사람이 눌러 바깥으로 나가는 자리이고,
#: 평문으로 나가는 주소를 우리 화면이 권할 이유가 없다. `javascript:` 와
#: `data:` 는 말할 것도 없다 — 그 둘이 이 검사가 있는 이유다.
LINK_SCHEME = "https"

#: 링크 주소에 끼울 수 있는 자리표. **모르는 것은 거절한다.**
#:
#: 그냥 두면 `{issue_id}` 를 `{issueId}` 로 적은 사람의 링크에 중괄호가
#: 글자로 실려 나간다. 등록할 때 걸러야 누른 사람이 깨진 주소를 안 본다.
PLACEHOLDERS = ("issue_key", "issue_id", "project_key")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

#: 앱이 놓을 수 있는 것의 종류.
KINDS = ("link", "panel")


@dataclass(frozen=True, slots=True)
class Slot:
    """자리 하나. `kind` 가 그 자리에 무엇을 놓을 수 있는지 정한다."""

    name: str
    kind: str
    #: 사람에게 보여 줄 설명 키가 아니라, 관리 화면이 읽는 설명이다.
    #: 카탈로그에 두지 않는 이유: 자리 이름은 개발자가 읽는 값이고
    #: 관리 화면에만 나온다 (i18n.md — 관리자가 지은 이름은 번역하지 않는다).
    where: str
    #: 자리표를 쓸 수 있는가. 이슈에 붙는 자리만 이슈 값을 안다.
    placeholders: tuple[str, ...] = ()


#: **자리 표.** 늘릴 때는 화면도 함께 고친다.
SLOTS: tuple[Slot, ...] = (
    Slot(
        name="issue.panel",
        kind="panel",
        where="이슈 상세 본문 아래의 칸",
        placeholders=("issue_key", "issue_id", "project_key"),
    ),
    Slot(
        name="issue.link",
        kind="link",
        where="이슈 상세의 링크 줄",
        placeholders=("issue_key", "issue_id", "project_key"),
    ),
    Slot(
        name="settings.link",
        kind="link",
        where="설정 좌측 목록",
    ),
)

_BY_NAME = {slot.name: slot for slot in SLOTS}


def slot(name: str) -> Slot:
    """자리를 찾는다. **모르는 이름은 거절한다** (파일 머리 참조)."""
    found = _BY_NAME.get(name)
    if found is None:
        raise ValidationError(
            "그런 자리는 없다.",
            code="plugins.unknown_slot",
            details={"known": [row.name for row in SLOTS]},
        )
    return found


def clean_slug(raw: str) -> str:
    """앱의 짧은 이름. 주소에 실리므로 좁게 받는다."""
    value = raw.strip().lower()
    if not _SLUG.match(value):
        raise ValidationError(
            "앱 이름은 영소문자·숫자·하이픈으로 2~64자다.",
            code="plugins.invalid_slug",
        )
    return value


def clean_label(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValidationError("자리에 붙일 이름이 필요하다.", code="plugins.label_required")
    if len(value) > 60:
        raise ValidationError("자리 이름이 너무 길다. 60자까지다.", code="plugins.label_too_long")
    return value


def check_kind(target: Slot, kind: str) -> str:
    """종류가 그 자리에 맞는지.

    링크 자리에 패널을 놓으면 화면은 아무것도 안 그린다. 등록할 때 막는다.
    """
    if kind not in KINDS:
        raise ValidationError("그런 종류는 없다.", code="plugins.unknown_kind")
    if kind != target.kind:
        raise ValidationError(
            "그 자리에는 그 종류를 놓을 수 없다.",
            code="plugins.kind_mismatch",
            details={"slot": target.name, "expects": target.kind},
        )
    return kind


def clean_url_template(target: Slot, raw: str) -> str:
    """링크 주소 틀. `https` 만, 아는 자리표만.

    **이 함수가 이 파일의 보안 검사다.** 등록한 주소는 우리 화면의 앵커가
    되므로, `javascript:` 하나가 통과하면 이슈를 여는 사람 전부가 그것을
    누를 수 있는 자리에 놓인다.
    """
    value = raw.strip()
    if not value:
        raise ValidationError("링크 주소가 필요하다.", code="plugins.url_required")
    if len(value) > 2000:
        raise ValidationError("링크 주소가 너무 길다.", code="plugins.url_too_long")

    scheme, _, rest = value.partition(":")
    if not rest or scheme.lower() != LINK_SCHEME:
        raise ValidationError(
            "링크는 https 로 시작해야 한다.",
            code="plugins.url_scheme",
            details={"scheme": LINK_SCHEME},
        )
    if not rest.startswith("//") or rest[2:].strip() == "":
        raise ValidationError("링크에 호스트가 없다.", code="plugins.url_no_host")

    unknown = sorted(
        {found for found in _PLACEHOLDER.findall(value) if found not in target.placeholders}
    )
    if unknown:
        raise ValidationError(
            "링크 주소에 모르는 자리표가 있다.",
            code="plugins.unknown_placeholder",
            details={"unknown": unknown, "known": list(target.placeholders)},
        )
    return value


def fill(template: str, values: dict[str, str]) -> str:
    """자리표를 채운다. **등록할 때 검사했으므로 여기서는 남기지 않는다.**

    `str.format` 을 쓰지 않는 이유: 그쪽은 `{0.__class__}` 같은 접근을
    허용하고, 틀은 밖에서 온 글이다.
    """
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def catalog() -> list[dict[str, object]]:
    """관리 화면이 고를 수 있는 자리 목록. 서버가 아는 것만 보여 준다."""
    return [
        {
            "name": row.name,
            "kind": row.kind,
            "where": row.where,
            "placeholders": list(row.placeholders),
        }
        for row in SLOTS
    ]


__all__ = [
    "KINDS",
    "LINK_SCHEME",
    "PLACEHOLDERS",
    "SLOTS",
    "Slot",
    "catalog",
    "check_kind",
    "clean_label",
    "clean_slug",
    "clean_url_template",
    "fill",
    "slot",
]
