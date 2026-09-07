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
    asset_folder,
    asset_targets,
    attachment_targets,
    content_disposition,
    encode_target,
    parse_document,
    read_archive,
    render_document,
    resolve_asset,
    rewrite_assets,
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
        archive = read_archive(data)
        assert [e.path for e in archive.entries] == ["docs/a.md"]
        assert archive.entries[0].document.title == "A"
        # `.md` 가 아닌 것은 문서가 아니지만 버리지도 않는다. 본문이 가리키면
        # 첨부로 흡수한다.
        assert set(archive.assets) == {"docs/image.png", "docs/notes.txt"}

    def test_shallow_entries_come_first(self) -> None:
        """부모가 자식보다 먼저 만들어져야 트리가 이어진다."""
        data = self._zip({"a/b/deep.md": b"deep", "top.md": b"top", "a/mid.md": b"mid"})
        assert [e.path for e in read_archive(data).entries] == ["top.md", "a/mid.md", "a/b/deep.md"]

    def test_non_utf8_files_are_skipped(self) -> None:
        # 추측해서 열면 깨진 글자가 문서로 들어앉고 되돌릴 방법이 없다.
        data = self._zip({"ok.md": b"fine", "broken.md": "한글".encode("euc-kr")})
        assert [e.path for e in read_archive(data).entries] == ["ok.md"]

    def test_rejects_a_broken_zip(self) -> None:
        with pytest.raises(ValidationError) as exc:
            read_archive(b"not a zip at all")
        assert exc.value.code == "wiki.invalid_archive"

    def test_write_then_read(self) -> None:
        data = write_archive([("a.md", "# A\n"), ("dir/b.md", "# B\n")])
        assert {e.path for e in read_archive(data).entries} == {"a.md", "dir/b.md"}


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


class TestAssetTargets:
    """본문이 가리키는 상대 경로 찾기.

    못 찾으면 ZIP 에 같이 넣은 그림이 통째로 깨진 링크가 된다.
    """

    def test_finds_images_and_links(self) -> None:
        body = "![그림](images/a.png)\n\n[문서](files/spec.pdf) 참고"
        assert asset_targets(body) == ["images/a.png", "files/spec.pdf"]

    def test_skips_absolute_and_schemed(self) -> None:
        body = "[웹](https://x.com/a.png) [뿌리](/a.png) [닻](#section) [첨부](attachment:1/a.png)"
        assert asset_targets(body) == []

    def test_skips_fenced_code(self) -> None:
        """문법을 설명한 예시가 링크로 읽히면 안 된다 (links.py 와 같은 이유)."""
        body = "```md\n![예시](images/a.png)\n```\n\n![진짜](images/b.png)"
        assert asset_targets(body) == ["images/b.png"]

    def test_angle_brackets(self) -> None:
        assert asset_targets("![공백 있는 이름](<images/a b.png>)") == ["images/a b.png"]

    def test_no_duplicates(self) -> None:
        assert asset_targets("![1](a.png) ![2](a.png)") == ["a.png"]


class TestResolveAsset:
    def test_relative_to_the_document(self) -> None:
        assert resolve_asset("docs/guide.md", "images/a.png") == "docs/images/a.png"
        assert resolve_asset("guide.md", "images/a.png") == "images/a.png"
        assert resolve_asset("docs/deep/guide.md", "../a.png") == "docs/a.png"

    def test_escaping_the_archive_is_refused(self) -> None:
        assert resolve_asset("guide.md", "../../etc/passwd") is None
        assert resolve_asset("docs/guide.md", "../../../x") is None

    def test_strips_query_and_fragment(self) -> None:
        assert resolve_asset("guide.md", "a.png?v=2") == "a.png"

    def test_decodes_percent_escapes(self) -> None:
        assert resolve_asset("guide.md", "images/%ED%95%9C%EA%B8%80.png") == "images/한글.png"


class TestRewriteAssets:
    def test_swaps_only_what_was_absorbed(self) -> None:
        """바꿀 목록을 미리 정해 두면 예시에 같은 글자가 있어도 안전하다."""
        body = "![a](images/a.png) ![b](images/b.png)"
        out = rewrite_assets(body, {"images/a.png": "attachment:1/a.png"})
        assert out == "![a](attachment:1/a.png) ![b](images/b.png)"

    def test_leaves_fenced_code_alone(self) -> None:
        body = "```md\n![x](a.png)\n```\n![x](a.png)"
        out = rewrite_assets(body, {"a.png": "attachment:1/a.png"})
        assert out == "```md\n![x](a.png)\n```\n![x](attachment:1/a.png)"

    def test_angle_brackets(self) -> None:
        out = rewrite_assets("![x](<a b.png>)", {"a b.png": "attachment:1/a b.png"})
        assert out == "![x](attachment:1/a b.png)"

    def test_nothing_to_do(self) -> None:
        assert rewrite_assets("![x](a.png)", {}) == "![x](a.png)"


class TestAttachmentTargets:
    """본문에 박힌 `attachment:` 참조 찾기. 내보내기가 여기서 담을 것을 정한다."""

    ID = "0b1c2d3e-4f56-7890-abcd-ef0123456789"

    def test_finds_the_id_and_the_filename(self) -> None:
        refs = attachment_targets(f"![그림](attachment:{self.ID}/a.png)")
        assert len(refs) == 1
        assert str(refs[0].attachment_id) == self.ID
        assert refs[0].filename == "a.png"
        assert refs[0].target == f"attachment:{self.ID}/a.png"

    def test_ignores_other_schemes(self) -> None:
        body = "[이슈](issue:ABC-1) [문서](page:x) [웹](https://x.com/a.png) [상대](a.png)"
        assert attachment_targets(body) == []

    def test_skips_fenced_code(self) -> None:
        """문법을 설명한 예시까지 담으면 안 된다."""
        body = f"```md\n![예시](attachment:{self.ID}/a.png)\n```"
        assert attachment_targets(body) == []

    def test_decodes_the_filename(self) -> None:
        refs = attachment_targets(f"![x](attachment:{self.ID}/%ED%95%9C%EA%B8%80.png)")
        assert refs[0].filename == "한글.png"

    def test_a_bare_id_has_no_filename(self) -> None:
        """파일명 없이 id 만 박혀 있어도 담을 수 있어야 한다."""
        refs = attachment_targets(f"![x](attachment:{self.ID})")
        assert refs[0].filename == ""

    def test_no_duplicates(self) -> None:
        body = f"![1](attachment:{self.ID}/a.png) ![2](attachment:{self.ID}/a.png)"
        assert len(attachment_targets(body)) == 1

    def test_a_lookalike_is_not_a_uuid(self) -> None:
        """글자 수만 맞는 것. 사람이 손으로 쓴 본문에 얼마든지 있을 수 있고,
        내보내기가 여기서 터지면 스페이스 전체를 못 받는다."""
        assert attachment_targets("[x](attachment:------------------------------------)") == []
        assert attachment_targets("[x](attachment:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)") == []


class TestAssetFolder:
    def test_sits_beside_the_document(self) -> None:
        """문서와 같은 자리라야 상대 경로가 그대로 맞는다."""
        assert asset_folder("docs/guide") == "docs/guide.assets"

    def test_does_not_collide_with_the_child_folder(self) -> None:
        """하위 문서는 `docs/guide/` 로 들어간다. 첨부가 그 안에 섞이면 안 된다."""
        assert not asset_folder("docs/guide").startswith("docs/guide/")


class TestEncodeTarget:
    """링크 대상 이스케이프. 최소한만 바꾼다."""

    def test_leaves_readable_text_alone(self) -> None:
        """`quote` 를 그대로 쓰면 한글 폴더명이 `%EC%95%88…` 이 된다."""
        assert encode_target("안내.assets/1/그림.png") == "안내.assets/1/그림.png"

    def test_escapes_what_breaks_the_link(self) -> None:
        assert encode_target("a b.png") == "a%20b.png"
        assert encode_target("x(1).png") == "x%281%29.png"

    def test_round_trips_through_resolve(self) -> None:
        for name in ("a b.png", "x(1).png", "100%.png", "한글.png"):
            assert resolve_asset("guide.md", encode_target(name)) == name

    def test_percent_goes_first(self) -> None:
        """안 그러면 파일명의 `%20` 이 되돌릴 때 공백이 되어 다른 파일이 된다."""
        assert resolve_asset("guide.md", encode_target("a%20b.png")) == "a%20b.png"
