"""`.md` 임포트·내보내기와 **라운드트립 계약**.

M2 완료 조건이 여기 걸려 있다 (wiki-markdown.md 8절). 이 테스트가 깨지면
에디터·임포터 기능 추가를 머지하지 않는다.

"의미상 동일"의 기준은 정규화다. 바이트 단위 동일은 애초에 불가능하고
(정규화가 목록 마커·표 패딩을 통일한다), 그걸 목표로 하면 정규화를 포기하게
된다. 대신 `normalize(원본) == normalize(왕복본)` 을 계약으로 삼는다.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.markdown import normalize
from ieum.modules.wiki.portable import (
    content_disposition,
    parse_document,
    read_archive,
    render_document,
    safe_archive_path,
    write_archive,
)


#: 라운드트립 코퍼스. 정본은 `packages/markdown/corpus.json` 이다 — 클라이언트의
#: WYSIWYG ↔ 마크다운 검사도 같은 문서 묶음을 쓴다. 한쪽에만 두면 다른 쪽이 못
#: 다루는 구조가 조용히 늘어난다.
def _corpus() -> list[tuple[str, str]]:
    path = Path(__file__).resolve().parents[3] / "packages" / "markdown" / "corpus.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(entry["name"], entry["source"]) for entry in data["documents"]]


CORPUS: list[tuple[str, str]] = _corpus()


class TestNormalizeIdempotency:
    """`normalize(normalize(x)) == normalize(x)`. 멱등하지 않으면 저장할 때마다 diff 가 생긴다."""

    @pytest.mark.parametrize(("name", "source"), CORPUS, ids=[c[0] for c in CORPUS])
    def test_idempotent(self, name: str, source: str) -> None:
        once = normalize(source)
        assert normalize(once) == once, name


class TestRoundTrip:
    """파일 → 문서 → 파일. 의미가 살아 있어야 한다."""

    @pytest.mark.parametrize(("name", "source"), CORPUS, ids=[c[0] for c in CORPUS])
    def test_body_survives(self, name: str, source: str) -> None:
        parsed = parse_document(source, fallback_title="T")
        rendered = render_document(
            title=parsed.title,
            body=parsed.body,
            labels=parsed.labels,
            front_matter=parsed.front_matter,
        )
        again = parse_document(rendered, fallback_title="T")
        assert again.body == parsed.body, name

    @pytest.mark.parametrize(("name", "source"), CORPUS, ids=[c[0] for c in CORPUS])
    def test_matches_the_normalized_original(self, name: str, source: str) -> None:
        parsed = parse_document(source, fallback_title="T")
        if source.lstrip().startswith("# "):
            # 첫 H1 은 제목으로 옮겨진다. 그 줄만 빠지고 나머지는 그대로여야
            # 한다 — 건너뛰면 이 경로가 아예 검사되지 않는다.
            without_h1 = source.split("\n", 1)[1] if "\n" in source else ""
            assert parsed.body == normalize(without_h1), name
        else:
            assert parsed.body == normalize(source), name

    def test_metadata_survives(self) -> None:
        source = "---\ntitle: 배포 절차\nlabels: [runbook, deploy]\nowner: infra\n---\n\n본문."
        parsed = parse_document(source)
        rendered = render_document(
            title=parsed.title,
            body=parsed.body,
            labels=parsed.labels,
            front_matter=parsed.front_matter,
        )
        again = parse_document(rendered)
        assert (again.title, again.labels) == ("배포 절차", ["runbook", "deploy"])
        # 우리가 모르는 키도 잃지 않는다. 남의 도구가 넣은 것일 수 있다.
        assert again.front_matter["owner"] == "infra"


class TestTitleResolution:
    def test_front_matter_wins(self) -> None:
        parsed = parse_document("---\ntitle: 지정한 제목\n---\n\n# 본문 제목\n\n내용")
        assert parsed.title == "지정한 제목"
        # front matter 가 이겼으므로 본문 H1 은 그대로 남는다.
        assert parsed.body.startswith("# 본문 제목")

    def test_first_h1_is_used_and_removed(self) -> None:
        """남겨 두면 화면에 제목이 두 번 뜬다."""
        parsed = parse_document("# 배포 절차\n\n내용이다.")
        assert parsed.title == "배포 절차"
        assert "배포 절차" not in parsed.body

    def test_falls_back_to_the_filename(self) -> None:
        parsed = parse_document("제목 없는 본문", fallback_title="deploy-runbook")
        assert parsed.title == "deploy-runbook"

    def test_only_the_first_h1_is_taken(self) -> None:
        parsed = parse_document("# 첫째\n\n본문\n\n# 둘째\n\n본문")
        assert parsed.title == "첫째"
        assert "# 둘째" in parsed.body

    def test_an_h1_below_text_is_still_taken(self) -> None:
        """문서 어디에 있든 **첫** H1 이 제목이다. 위치를 따지지 않는다."""
        parsed = parse_document("앞머리\n\n# 뒤의 제목")
        assert parsed.title == "뒤의 제목"
        assert "앞머리" in parsed.body


class TestFrontMatter:
    def test_mid_document_dashes_are_not_front_matter(self) -> None:
        """본문 중간의 `---` 는 수평선이지 메타데이터가 아니다."""
        source = "본문\n\n---\nnot: metadata\n---\n\n더"
        parsed = parse_document(source, fallback_title="T")
        assert parsed.front_matter == {}
        assert "not: metadata" in parsed.body

    def test_broken_yaml_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            parse_document("---\n: : :\n\ta: [1,\n---\n\n본문")
        assert exc.value.code == "wiki.invalid_front_matter"

    def test_scalar_front_matter_is_treated_as_body(self) -> None:
        parsed = parse_document("---\n그냥 문자열\n---\n\n본문", fallback_title="T")
        assert parsed.front_matter == {}

    def test_labels_accept_a_comma_string(self) -> None:
        parsed = parse_document("---\ntitle: T\nlabels: 'a, B , a'\n---\n\nx")
        # 소문자로 맞추고 순서를 유지한다. 중복은 서비스가 지운다.
        assert parsed.labels == ["a", "b", "a"]

    def test_title_and_labels_leave_the_front_matter(self) -> None:
        """두 곳에 있으면 어느 쪽이 진실인지 규칙이 하나 더 생긴다."""
        parsed = parse_document("---\ntitle: T\nlabels: [x]\nother: keep\n---\n\nbody")
        assert parsed.front_matter == {"other": "keep"}


class TestArchive:
    def _zip(self, files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_reads_markdown_and_skips_the_rest(self) -> None:
        data = self._zip(
            {
                "docs/a.md": b"# A\n\nbody",
                "docs/image.png": b"\x89PNG",
                "docs/notes.txt": b"not markdown",
            }
        )
        entries = read_archive(data)
        assert [e.path for e in entries] == ["docs/a.md"]
        assert entries[0].document.title == "A"

    def test_shallow_entries_come_first(self) -> None:
        """부모가 자식보다 먼저 만들어져야 트리가 이어진다."""
        data = self._zip({"a/b/deep.md": b"deep", "top.md": b"top", "a/mid.md": b"mid"})
        assert [e.path for e in read_archive(data)] == ["top.md", "a/mid.md", "a/b/deep.md"]

    def test_non_utf8_files_are_skipped(self) -> None:
        # 추측해서 열면 깨진 글자가 문서로 들어앉고 되돌릴 방법이 없다.
        data = self._zip({"ok.md": b"fine", "broken.md": "한글".encode("euc-kr")})
        assert [e.path for e in read_archive(data)] == ["ok.md"]

    def test_rejects_a_broken_zip(self) -> None:
        with pytest.raises(ValidationError) as exc:
            read_archive(b"not a zip at all")
        assert exc.value.code == "wiki.invalid_archive"

    def test_write_then_read(self) -> None:
        data = write_archive([("a.md", "# A\n"), ("dir/b.md", "# B\n")])
        assert {e.path for e in read_archive(data)} == {"a.md", "dir/b.md"}


class TestSafeArchivePath:
    @pytest.mark.parametrize(
        "raw",
        ["../escape.md", "a/../../escape.md", "/absolute.md", "C:/win.md", "__MACOSX/x.md"],
    )
    def test_rejects_dangerous_paths(self, raw: str) -> None:
        """ZIP 은 남이 만든 파일이다. 경로를 믿으면 트리 밖에 문서를 만든다."""
        assert safe_archive_path(raw) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a/b.md", "a/b.md"),
            ("./a/b.md", "a/b.md"),
            ("a//b.md", "a/b.md"),
            ("a\\b.md", "a/b.md"),
        ],
    )
    def test_normalizes_ordinary_paths(self, raw: str, expected: str) -> None:
        assert safe_archive_path(raw) == expected


class TestRender:
    def test_always_writes_front_matter(self) -> None:
        out = render_document(title="T", body="", labels=[], front_matter={})
        assert out.startswith("---\ntitle: T\n---")

    def test_ends_with_a_newline(self) -> None:
        """파일이므로 개행으로 끝난다. 정규화기는 뗀다 — 거긴 DB 컬럼이다."""
        assert render_document(title="T", body="body", labels=[], front_matter={}).endswith("\n")

    def test_korean_is_not_escaped(self) -> None:
        out = render_document(title="배포 절차", body="", labels=[], front_matter={})
        assert "배포 절차" in out

    def test_title_is_not_duplicated_as_an_h1(self) -> None:
        """front matter 가 제목의 유일한 자리다."""
        out = render_document(title="T", body="본문", labels=[], front_matter={})
        assert "# T" not in out


class TestContentDisposition:
    """한글 파일명. HTTP 헤더는 latin-1 밖에 못 담는다."""

    def test_ascii_name_passes_through(self) -> None:
        value = content_disposition("deploy-runbook.md")
        assert 'filename="deploy-runbook.md"' in value

    def test_korean_name_survives_as_rfc5987(self) -> None:
        """한글 제목 문서는 slug 도 한글이다 (로마자로 옮기지 않는다).

        그대로 헤더에 넣으면 응답을 만들다 터진다 — 실제로 500 이었다.
        """
        value = content_disposition("배포-절차.md")
        assert value.encode("latin-1")  # 헤더로 나갈 수 있어야 한다
        assert "filename*=UTF-8''" in value
        assert "%EB%B0%B0%ED%8F%AC" in value

    def test_a_name_that_folds_away_still_has_a_fallback(self) -> None:
        value = content_disposition("한글.md")
        assert 'filename="document.md"' in value

    def test_quotes_cannot_break_out_of_the_header(self) -> None:
        value = content_disposition('a"; drop=1; x=".md')
        assert value.count('"') == 2

    def test_newlines_cannot_inject_a_header(self) -> None:
        value = content_disposition("a\r\nX-Evil: 1.md")
        assert "\r" not in value and "\n" not in value

    def test_the_extension_is_kept(self) -> None:
        assert content_disposition("ENG.zip").startswith('attachment; filename="ENG.zip"')
