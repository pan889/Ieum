"""판 사이 본문 비교 (wiki-markdown.md 9절).

텍스트 라인 diff 다. 렌더 결과를 비교하지 않는다 — 정본이 마크다운이므로
사람이 실제로 고친 것과 diff 가 일치한다. 표와 코드블록에 라인 diff 가 잘
맞는 것도 마크다운을 고른 부수 이득이다.

본문은 이미 정규화돼 있다(normalize 는 저장 경로가 단독 소유). 그래서 목록
마커나 표 패딩 같은 서식 흔들림이 diff 에 섞이지 않는다 — 정규화가 없었으면
한 글자만 고쳐도 문서 절반이 바뀐 것으로 보였을 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

Op = Literal["equal", "insert", "delete"]

#: 한 번에 돌려주는 줄 수 상한. 큰 문서 둘을 통째로 비교하면 응답이 커진다.
MAX_LINES = 5000

#: 바뀐 자리 둘레에 붙여 보여 줄 줄 수. 통짜 문서를 다 보여 주면
#: 무엇이 바뀌었는지 오히려 안 보인다.
CONTEXT_LINES = 3


@dataclass(frozen=True, slots=True)
class DiffLine:
    op: Op
    #: 이전 판의 줄 번호. 추가된 줄이면 None.
    old_number: int | None
    #: 이후 판의 줄 번호. 지워진 줄이면 None.
    new_number: int | None
    text: str


@dataclass(frozen=True, slots=True)
class DiffResult:
    lines: list[DiffLine]
    added: int
    removed: int
    #: 상한에 걸려 잘렸으면 True. 화면이 "여기까지"라고 말해야 한다.
    truncated: bool


def diff_lines(before: str, after: str, *, context: int = CONTEXT_LINES) -> DiffResult:
    """두 본문의 줄 차이. 바뀐 자리 둘레만 남긴다."""
    old = before.split("\n") if before else []
    new = after.split("\n") if after else []
    truncated = len(old) > MAX_LINES or len(new) > MAX_LINES
    old, new = old[:MAX_LINES], new[:MAX_LINES]

    rows: list[DiffLine] = []
    added = removed = 0
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            rows.extend(_equal_rows(old, i1, i2, j1, context))
            continue
        # replace 는 지운 뒤 넣은 것으로 편다. 한 줄 안에서 무엇이 바뀌었는지는
        # 화면이 필요하면 따로 낸다 — 여기서는 줄 단위가 계약이다.
        for offset in range(i2 - i1):
            rows.append(
                DiffLine(
                    op="delete", old_number=i1 + offset + 1, new_number=None, text=old[i1 + offset]
                )
            )
            removed += 1
        for offset in range(j2 - j1):
            rows.append(
                DiffLine(
                    op="insert", old_number=None, new_number=j1 + offset + 1, text=new[j1 + offset]
                )
            )
            added += 1
    return DiffResult(lines=rows, added=added, removed=removed, truncated=truncated)


def _equal_rows(old: list[str], i1: int, i2: int, j1: int, context: int) -> list[DiffLine]:
    """같은 구간에서 앞뒤 `context` 줄만 남긴다. 가운데는 버린다."""
    length = i2 - i1
    keep: list[int] = (
        list(range(length))
        if length <= context * 2
        else [*range(context), *range(length - context, length)]
    )
    return [
        DiffLine(op="equal", old_number=i1 + k + 1, new_number=j1 + k + 1, text=old[i1 + k])
        for k in keep
    ]


__all__ = ["CONTEXT_LINES", "MAX_LINES", "DiffLine", "DiffResult", "Op", "diff_lines"]
