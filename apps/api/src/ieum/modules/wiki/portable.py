"""`.md` 임포트·내보내기.

M2 완료 조건이 여기 걸려 있다: 임의의 `.md` 묶음을 올려 그대로 읽히고,
편집 후 내보낸 `.md` 가 원본과 **의미상 동일**해야 한다.

"의미상 동일"의 기준은 정규화다. 바이트 단위 동일은 애초에 불가능하고
(정규화가 목록 마커·표 패딩을 통일한다), 그걸 목표로 하면 정규화를 포기하게
된다. 대신 `normalize(원본) == normalize(왕복본)` 을 계약으로 삼는다.
"""

from __future__ import annotations

import io
import posixpath
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import yaml

from ieum.core.exceptions import ValidationError
from ieum.core.markdown import MAX_LENGTH, normalize

#: front matter 블록. 파일 **맨 앞**에서만 인정한다 — 본문 중간의 `---` 는
#: 수평선이지 메타데이터가 아니다.
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

#: 첫 H1. 제목을 못 찾았을 때 여기서 가져온다.
_FIRST_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)

#: 묶음 하나에 담을 수 있는 파일 수. 사고로 올린 큰 ZIP 이 워커를 잡지 않게.
MAX_ARCHIVE_FILES = 500
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

_MD_SUFFIXES = (".md", ".markdown")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str
    body: str
    front_matter: dict[str, Any]
    labels: list[str] = field(default_factory=list)


def parse_document(text: str, *, fallback_title: str = "Untitled") -> ParsedDocument:
    """`.md` 한 편을 읽는다.

    제목 결정 순서는 front matter `title` → 첫 H1 → 파일명이다
    (wiki-markdown.md 2절). 첫 H1 을 제목으로 쓴 경우 본문에서 **떼어낸다** —
    남겨 두면 화면에 제목이 두 번 뜬다.
    """
    if len(text) > MAX_LENGTH:
        raise ValidationError(
            f"문서는 {MAX_LENGTH}자 이하여야 한다.",
            code="wiki.import_too_large",
            details={"max": MAX_LENGTH, "length": len(text)},
        )

    front_matter, body = _split_front_matter(text)

    title = _string_or_none(front_matter.get("title"))
    if title is None:
        match = _FIRST_H1.search(body)
        if match:
            title = match.group(1).strip()
            body = body[: match.start()] + body[match.end() :]
    title = (title or fallback_title).strip() or fallback_title

    labels = _string_list(front_matter.get("labels"))
    # 소비한 키는 front matter 에 남기지 않는다. 남기면 제목이 두 곳에 있고
    # 어느 쪽이 진실인지 규칙이 하나 더 생긴다.
    remaining = {k: v for k, v in front_matter.items() if k not in {"title", "labels"}}

    return ParsedDocument(
        title=title[:500],
        body=normalize(body),
        front_matter=remaining,
        labels=labels,
    )


def render_document(
    *, title: str, body: str, labels: list[str], front_matter: dict[str, Any]
) -> str:
    """페이지를 `.md` 파일로. 항상 front matter 를 붙인다.

    제목을 본문 H1 으로 넣지 않는다 — 다시 임포트하면 그 H1 이 제목으로
    소비되므로 왕복은 성립하지만, 화면에서 제목이 두 번 보인다. front matter
    가 제목의 유일한 자리다.

    파일이므로 끝에 개행을 붙인다(정규화기는 뗀다 — 거긴 DB 컬럼이다).
    """
    meta: dict[str, Any] = {"title": title, **front_matter}
    if labels:
        meta["labels"] = labels
    header = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip("\n")
    return f"---\n{header}\n---\n\n{body.rstrip()}\n" if body.strip() else f"---\n{header}\n---\n"


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValidationError(
            "front matter 를 읽을 수 없다.",
            code="wiki.invalid_front_matter",
            details={"reason": str(exc)[:200]},
        ) from exc
    # 스칼라나 목록이 오면 메타데이터가 아니다. 본문으로 되돌린다.
    if not isinstance(loaded, dict):
        return {}, text
    return loaded, text[match.end() :]


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip().lower() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return []


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """ZIP 안의 `.md` 하나. `path` 는 폴더 구조를 그대로 담는다."""

    path: str
    document: ParsedDocument


def read_archive(data: bytes) -> list[ArchiveEntry]:
    """ZIP 에서 `.md` 를 꺼낸다. 폴더 구조가 곧 문서 트리다.

    `.md` 가 아닌 파일은 건너뛴다. 첨부로 흡수하는 것은 다음 단계다 —
    여기서 하면 임포트가 스토리지까지 걸치게 된다.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "묶음이 너무 크다.",
            code="wiki.import_too_large",
            details={"max": MAX_ARCHIVE_BYTES},
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationError("ZIP 을 읽을 수 없다.", code="wiki.invalid_archive") from exc

    entries: list[ArchiveEntry] = []
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_ARCHIVE_FILES:
            raise ValidationError(
                f"묶음에는 파일 {MAX_ARCHIVE_FILES}개까지 담을 수 있다.",
                code="wiki.import_too_large",
                details={"max": MAX_ARCHIVE_FILES},
            )
        for member in members:
            safe = safe_archive_path(member.filename)
            if safe is None or not safe.lower().endswith(_MD_SUFFIXES):
                continue
            raw = archive.read(member)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # UTF-8 이 아니면 건너뛴다. 추측해서 열면 깨진 글자가
                # 문서로 들어앉고 되돌릴 방법이 없다.
                continue
            stem = posixpath.splitext(posixpath.basename(safe))[0]
            entries.append(
                ArchiveEntry(path=safe, document=parse_document(text, fallback_title=stem))
            )
    # 얕은 것부터. 부모가 자식보다 먼저 만들어져야 트리가 이어진다.
    entries.sort(key=lambda e: (e.path.count("/"), e.path))
    return entries


def safe_archive_path(name: str) -> str | None:
    """ZIP 안의 경로를 안전하게 만든다. 못 만들면 None.

    `../` 와 절대 경로를 막는다. ZIP 은 남이 만든 파일이라, 경로를 그대로
    믿으면 트리 밖으로 문서를 만들거나(zip slip) 이름 충돌을 일으킨다.
    """
    cleaned = name.replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ":" in cleaned.split("/")[0]:
        return None
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        # macOS 가 넣는 메타데이터 폴더. 문서가 아니다.
        if part == "__MACOSX":
            return None
        parts.append(part)
    return "/".join(parts) or None


#: 파일명에서 뺄 것. 따옴표·역슬래시·제어문자는 헤더를 깨뜨린다.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def content_disposition(filename: str) -> str:
    """내려받기 헤더 값. **한글 파일명을 지킨다.**

    HTTP 헤더는 latin-1 밖에 못 담는다. 한글 제목의 문서는 slug 도 한글이라
    (로마자로 옮기지 않는다 — slug.py 참고) 파일명을 그대로 넣으면 응답을
    만들다 터진다. 실제로 한글 제목 문서 내보내기가 500 이었다.

    RFC 5987 의 `filename*` 로 UTF-8 을 싣고, 그걸 모르는 오래된 클라이언트를
    위해 ASCII 로 접은 `filename` 을 함께 준다.
    """
    stem, ext = posixpath.splitext(filename)
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    safe_stem = _UNSAFE_IN_FILENAME.sub("-", folded).strip("-.") or "document"
    safe_ext = _UNSAFE_IN_FILENAME.sub("", ext) or ".md"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{safe_stem}{safe_ext}\"; filename*=UTF-8''{quoted}"


def write_archive(files: list[tuple[str, str]]) -> bytes:
    """`(경로, 내용)` 목록을 ZIP 으로. 경로에는 `.md` 가 이미 붙어 있어야 한다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files:
            archive.writestr(path, content)
    return buffer.getvalue()


__all__ = [
    "ArchiveEntry",
    "ParsedDocument",
    "content_disposition",
    "parse_document",
    "read_archive",
    "render_document",
    "safe_archive_path",
    "write_archive",
]
