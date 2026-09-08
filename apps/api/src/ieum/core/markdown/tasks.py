"""본문의 태스크 리스트 — `- [ ]` 항목을 1급으로 다룬다 (B12).

## 왜 파서와 줄을 나눠 쓰나

**무엇이 태스크인가는 파서가 정한다.** 정규식으로 `- [ ]` 를 찾으면 코드
블록 안의 예시도 태스크가 되고, 문서에 문법을 설명해 둔 사람의 "내 할 일" 에
그 예시가 뜬다 (`mentions.py` 가 같은 이유로 토큰을 본다).

**그 태스크의 상태와 글자는 줄에서 읽는다.** `tasklists` 플러그인은 체크박스를
날 HTML(`html_inline`)로 내보내므로, 거기서 상태를 뽑으려면 HTML 문자열을
헤집어야 한다. 이미 "이 줄은 태스크다" 를 파서가 보증했으므로, 그 줄의 마커만
읽으면 된다.

## 왜 별도 표에 담지 않고 본문에 적나

마크다운이 정본이다(ADR-0008). 담당자·기한을 별도 표에 두면 사람이 문서를
손으로 고치는 순간(임포트·소스 모드 편집·되돌리기) 두 벌이 어긋난다. 그래서
담당자는 **본문의 멘션**이고(`[@Alice](user:<uuid>)`) 기한은 `due:YYYY-MM-DD`
다. 둘 다 마크다운으로 읽히고, 다른 도구로 열어도 뜻이 보인다.

담당자를 멘션으로 두는 것에는 덤이 있다: 이미 있는 멘션 알림이 그대로
동작한다 — 태스크를 배정하는 것이 곧 그 사람을 부르는 것이다.

## 고칠 때는 그 줄만 고친다

`set_done` 은 마커 두 글자만 바꾼다. 본문을 파싱해서 다시 직렬화하면 사람이
쓴 공백·불릿 종류·줄바꿈이 조용히 정규화되고, 그 diff 가 "무엇이 바뀌었나" 를
가린다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from markdown_it.token import Token

from ieum.core.markdown.dialect import parser

#: 한 문서에서 인정하는 태스크 수. 넘으면 앞에서부터 자른다.
#:
#: 상한을 두는 이유는 집계다: 문서마다 유도 표에 행이 쌓이고, 그 표를 읽는
#: 화면은 사람 단위로 돈다. 한 문서가 만 줄짜리 체크리스트여도 그 문서 하나가
#: 남의 "내 할 일" 을 느리게 만들면 안 된다.
MAX_TASKS = 200

#: 태스크 항목의 앞머리: 들여쓰기 + 불릿 + 공백 + `[ ]`.
#:
#: 파서가 이미 "이 줄은 태스크다" 를 보증한 뒤에만 쓴다. 그래서 이 정규식은
#: **찾는** 것이 아니라 **읽는** 것이고, 코드 블록을 걱정할 필요가 없다.
_MARKER = re.compile(r"^(\s*[-*+]\s+\[)([ xX])(\])")

#: 기한. `due:` 를 붙이는 이유: 날짜만 두면 본문의 다른 날짜와 구별할 수 없고,
#: 이모지로 두면 다른 도구에서 안 읽힌다.
_DUE = re.compile(r"(?:^|\s)due:(\d{4})-(\d{2})-(\d{2})(?=\s|$)")

_MENTION = re.compile(r"\[@[^\]]*\]\(user:([0-9a-fA-F-]{36})\)")


@dataclass(frozen=True, slots=True)
class Task:
    """본문의 태스크 한 줄."""

    #: 0부터 세는 원문 줄 번호. **이것이 태스크의 신원이다.**
    #:
    #: 몇 번째 태스크인가로 세지 않는 이유: 화면과 서버가 각자 파싱해서
    #: 순서를 세면 중첩 목록에서 어긋날 수 있고, 어긋난 채로 체크하면
    #: **다른 줄이 바뀐다.** 줄 번호는 한쪽(서버)만 세면 된다.
    line: int
    done: bool
    #: 사람이 읽는 글. 마커와 `due:` 는 뺐고 멘션은 마크다운으로 남긴다 —
    #: 집계 화면이 그대로 렌더해서 담당자를 링크로 보여 줄 수 있게.
    text: str
    #: 첫 멘션. 둘 이상 적혀 있어도 담당자는 하나다.
    assignee: UUID | None
    due: date | None


def parse_tasks(body: str) -> list[Task]:
    """본문의 태스크를 등장 순서대로.

    코드 블록 안의 `- [ ]` 는 태스크가 아니다 — 파서가 그것을 목록으로 읽지
    않기 때문에 자연히 빠진다.
    """
    if "[" not in body:
        return []

    lines = body.split("\n")
    found: list[Task] = []
    tokens = parser().parse(body)
    for index, token in enumerate(tokens):
        if token.type != "list_item_open":
            continue
        classes = token.attrGet("class")
        if not isinstance(classes, str) or "task-list-item" not in classes:
            continue
        if token.map is None:
            continue
        line = token.map[0]
        if line >= len(lines):  # pragma: no cover - map 이 본문을 벗어나면 파서 버그다
            continue
        marker = _MARKER.match(lines[line])
        if marker is None:  # pragma: no cover - 파서가 태스크라 했는데 마커가 없다
            continue
        meta = _meta(_inline_after(tokens, index))
        found.append(
            Task(
                line=line,
                done=marker.group(2) in ("x", "X"),
                text=meta.text,
                assignee=meta.assignee,
                due=meta.due,
            )
        )
        if len(found) >= MAX_TASKS:
            break
    return found


def set_done(body: str, line: int, done: bool) -> str:
    """그 줄의 마커만 바꾼다.

    돌려주는 것은 **한 글자만 다른 본문**이다. 다시 직렬화하지 않으므로
    들여쓰기·불릿 종류·줄 끝 공백이 그대로 남고, 판 사이 diff 가 정확히
    바뀐 것만 가리킨다.
    """
    lines = body.split("\n")
    if line < 0 or line >= len(lines):
        raise ValueError("본문에 없는 줄이다")
    marker = _MARKER.match(lines[line])
    if marker is None:
        raise ValueError("그 줄은 태스크가 아니다")
    lines[line] = _MARKER.sub(
        lambda m: f"{m.group(1)}{'x' if done else ' '}{m.group(3)}", lines[line], count=1
    )
    return "\n".join(lines)


def _inline_after(tokens: Sequence[Token], index: int) -> str:
    """`list_item_open` 다음에 오는 항목 본문(마커는 플러그인이 이미 뗐다)."""
    for token in tokens[index + 1 : index + 4]:
        if token.type == "inline":
            return token.content
        if token.type == "list_item_open":
            # 내용 없이 곧바로 중첩된 항목. 글자가 없는 태스크다.
            break
    return ""


@dataclass(frozen=True, slots=True)
class _Meta:
    text: str
    assignee: UUID | None
    due: date | None


def _meta(content: str) -> _Meta:
    """항목 본문에서 기한·담당자를 읽고, 글자에서 기한 표기를 뺀다."""
    due: date | None = None
    match = _DUE.search(content)
    if match is not None:
        try:
            due = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            # `due:2026-13-40` 같은 것. **글자로 남긴다** — 조용히 버리면
            # 사람은 기한을 적었다고 믿고 아무 일도 안 일어난다.
            due = None
        else:
            content = content[: match.start()] + content[match.end() :]

    mention = _MENTION.search(content)
    assignee: UUID | None = None
    if mention is not None:
        try:
            assignee = UUID(mention.group(1))
        except ValueError:  # pragma: no cover - 정규식이 36자만 잡는다
            assignee = None

    return _Meta(text=" ".join(content.split()), assignee=assignee, due=due)


__all__ = ["MAX_TASKS", "Task", "parse_tasks", "set_done"]
