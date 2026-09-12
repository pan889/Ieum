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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

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

#: 내보내기에 함께 담을 첨부의 총량. 넘치면 그 파일만 빼고, 뺀 목록을
#: 묶음 안에 적어 둔다 — 조용히 빠지면 옮긴 쪽에서 영영 모른다.
MAX_EXPORT_ASSET_BYTES = 40 * 1024 * 1024
#: 뺀 첨부를 적어 두는 파일.
SKIPPED_MANIFEST = "_SKIPPED_ATTACHMENTS.txt"

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


#: 본문의 링크·이미지 대상. `](여기)` 만 본다 — 참조 스타일 링크는 드물고,
#: 못 잡아도 원문 그대로 남을 뿐이라 문서가 깨지지 않는다.
_LINK_TARGET = re.compile(r"\]\(\s*(<[^>]*>|[^)\s]+)")

#: 코드 울타리. 안쪽은 예시라 건드리지 않는다.
_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")

#: 스킴이 붙었으면 우리 파일이 아니다.
_SCHEME = re.compile(r"\A[a-zA-Z][a-zA-Z0-9+.-]*:")


#: 본문에 박힌 첨부 참조. 저장은 스킴으로 한다 — 서명된 주소는 몇 분 뒤 죽고,
#: 내보낸 `.md` 가 그 서버에 묶인다 (web 의 markdown/attachments.ts 와 짝).
_ATTACHMENT = re.compile(r"\Aattachment:([0-9a-fA-F-]{36})(?:/(.*))?\Z")


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """본문 한 곳이 가리키는 첨부. `target` 은 본문에 적힌 글자 그대로다."""

    target: str
    attachment_id: UUID
    filename: str


@dataclass(frozen=True, slots=True)
class Archive:
    """묶음에서 꺼낸 것. 문서와, 문서가 가리킬 수 있는 나머지 파일들."""

    entries: list[ArchiveEntry]
    #: 경로 → 바이트. `.md` 가 아닌 파일 전부. 실제로 참조된 것만 흡수한다.
    assets: dict[str, bytes]


def link_targets(body: str) -> list[str]:
    """본문이 가리키는 대상 전부. 등장 순서대로, 중복 없이.

    울타리 안은 건너뛴다 — 문법을 설명한 코드 예시가 링크로 읽히면 안 된다
    (links.py 와 같은 이유).
    """
    found: list[str] = []
    seen: set[str] = set()
    fence: str | None = None

    for line in body.split("\n"):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[:3]
            elif token.startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        for match in _LINK_TARGET.finditer(line):
            target = match.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if not target or target in seen:
                continue
            seen.add(target)
            found.append(target)
    return found


def asset_targets(body: str) -> list[str]:
    """본문이 가리키는 **상대 경로**만. 임포트가 흡수할 후보다."""
    # 스킴이 있거나(`https:`·`attachment:`) 절대 경로면 우리 파일이 아니다.
    return [
        target
        for target in link_targets(body)
        if not _SCHEME.match(target) and not target.startswith(("/", "#"))
    ]


def attachment_targets(body: str) -> list[AttachmentRef]:
    """본문에 박힌 `attachment:<id>/<파일명>` 참조. 내보내기가 쓴다."""
    refs: list[AttachmentRef] = []
    for target in link_targets(body):
        match = _ATTACHMENT.match(target)
        if match is None:
            continue
        try:
            attachment_id = UUID(match.group(1))
        except ValueError:
            # 글자 수만 맞고 UUID 는 아니다. 사람이 손으로 쓴 본문이므로
            # 얼마든지 있을 수 있다 — 내보내기가 여기서 터지면 안 된다.
            continue
        refs.append(
            AttachmentRef(
                target=target,
                attachment_id=attachment_id,
                filename=unquote(match.group(2) or ""),
            )
        )
    return refs


def resolve_asset(document_path: str, target: str) -> str | None:
    """문서 위치를 기준으로 상대 경로를 묶음 안의 경로로. 밖으로 나가면 None."""
    cleaned = unquote(target.split("#", 1)[0].split("?", 1)[0]).strip()
    if not cleaned:
        return None
    base = posixpath.dirname(document_path)
    resolved = posixpath.normpath(posixpath.join(base, cleaned))
    # `../../etc/passwd` 같은 것. 묶음 밖은 아예 안 본다.
    if resolved.startswith("../") or resolved in {"..", "."} or resolved.startswith("/"):
        return None
    return resolved


def asset_folder(page_path: str) -> str:
    """문서 옆에 두는 첨부 폴더. `docs/guide.md` → `docs/guide.assets`.

    문서 파일과 같은 자리에 두어야 다시 올렸을 때 상대경로가 그대로 맞는다.
    하위 문서 폴더(`docs/guide/`)와도 겹치지 않는다.
    """
    return f"{page_path}.assets"


#: 링크 대상에 그대로 두면 문법이 깨지는 글자. 나머지는 손대지 않는다 —
#: `quote` 를 그대로 쓰면 `안내.assets` 가 `%EC%95%88…` 이 되어 사람이 못 읽는다.
_UNSAFE_IN_TARGET = {" ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E"}


def encode_target(path: str) -> str:
    """링크 대상으로 쓸 수 있게 **최소한만** 이스케이프한다.

    `resolve_asset` 이 되돌린다(unquote). `%` 를 먼저 바꾸지 않으면 파일명에
    있던 `%20` 이 되돌릴 때 공백이 되어 다른 파일을 가리킨다.
    """
    out = path.replace("%", "%25")
    for char, escaped in _UNSAFE_IN_TARGET.items():
        out = out.replace(char, escaped)
    return out


def rewrite_assets(body: str, replacements: dict[str, str]) -> str:
    """본문의 상대 경로를 새 주소로. **실제로 흡수한 것만** 바꾼다.

    바꿀 목록을 미리 정해 두면 코드 예시에 우연히 같은 글자가 있어도 안전하다
    — 그 파일이 묶음에 실제로 있고 첨부가 된 경우에만 목록에 오른다.
    """
    if not replacements:
        return body

    def _swap(match: re.Match[str]) -> str:
        raw = match.group(1)
        target = raw[1:-1].strip() if raw.startswith("<") and raw.endswith(">") else raw
        replaced = replacements.get(target)
        return match.group(0) if replaced is None else f"]({replaced}"

    out: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[:3]
            elif token.startswith(fence):
                fence = None
            out.append(line)
            continue
        out.append(line if fence is not None else _LINK_TARGET.sub(_swap, line))
    return "\n".join(out)


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, *, already: int) -> bytes:
    """한 멤버를 읽되 **푼 뒤의 크기**를 센다.

    `MAX_ARCHIVE_BYTES` 는 압축된 크기만 잰다. deflate 는 0 으로 채운 파일을
    1000:1 넘게 줄이므로, 1MB 짜리 ZIP 하나가 수십 GB 로 풀릴 수 있다 —
    개수 제한도 크기 제한도 통과하고, `archive.read()` 가 그것을 통째로
    메모리에 올린 뒤에 프로세스가 죽는다.

    헤더의 `file_size` 는 만든 쪽이 적은 값이라 믿지 않는다. 조각으로 읽으며
    **누적 해제량**을 직접 센다. 상한은 압축 상한과 같은 값을 쓴다 — 풀어서
    50MB 를 넘는 위키 묶음은 지금 다루는 물건이 아니다.
    """
    chunk = 1 << 20
    out = bytearray()
    with archive.open(member) as stream:
        while True:
            piece = stream.read(chunk)
            if not piece:
                break
            out += piece
            if already + len(out) > MAX_ARCHIVE_BYTES:
                raise ValidationError(
                    "묶음을 풀면 너무 크다.",
                    code="wiki.import_too_large",
                    details={"max": MAX_ARCHIVE_BYTES},
                )
    return bytes(out)


def read_archive(data: bytes) -> Archive:
    """ZIP 에서 `.md` 와 나머지 파일을 꺼낸다. 폴더 구조가 곧 문서 트리다.

    `.md` 가 아닌 파일도 들고 온다. 문서가 가리키는 것만 첨부로 흡수하고,
    아무도 안 가리키는 파일은 버린다 — 묶음에 딸려 온 `.DS_Store` 까지
    첨부가 되면 안 된다.
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
        assets: dict[str, bytes] = {}
        unpacked = 0
        for member in members:
            safe = safe_archive_path(member.filename)
            if safe is None:
                continue
            raw = _read_member(archive, member, already=unpacked)
            unpacked += len(raw)
            if not safe.lower().endswith(_MD_SUFFIXES):
                assets[safe] = raw
                continue
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
    return Archive(entries=entries, assets=assets)


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


def write_archive(files: Sequence[tuple[str, str | bytes]]) -> bytes:
    """`(경로, 내용)` 목록을 ZIP 으로.

    글자와 바이트를 함께 담는다 — 문서 옆에 그 문서의 첨부가 들어간다.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files:
            archive.writestr(path, content)
    return buffer.getvalue()


__all__ = [
    "MAX_EXPORT_ASSET_BYTES",
    "SKIPPED_MANIFEST",
    "Archive",
    "ArchiveEntry",
    "AttachmentRef",
    "ParsedDocument",
    "asset_folder",
    "asset_targets",
    "attachment_targets",
    "content_disposition",
    "encode_target",
    "link_targets",
    "parse_document",
    "read_archive",
    "render_document",
    "resolve_asset",
    "rewrite_assets",
    "safe_archive_path",
    "write_archive",
]
