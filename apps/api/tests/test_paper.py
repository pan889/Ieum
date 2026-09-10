"""인쇄물 조판 — PDF·Word (B17). **DB 를 세우지 않는다.**

`core.paper` 는 마크다운과 첨부 바이트만 받는 순수 함수다. 권한과 첨부 읽기는
`modules/wiki/service.py` 가 하고 그쪽은 실제 Postgres 로 시험한다
(`test_wiki_service.py`).

여기서 붙잡는 것 — 앞의 둘이 이 파일의 이유다:

- **조판기에게 네트워크를 주지 않는다.** WeasyPrint 는 기본적으로 문서에 적힌
  주소를 가져오므로, 본문에 `http://169.254.169.254/...` 를 쓴 사람이 서버의
  자리에서 요청을 보낼 수 있다(SSRF).
- **태스크 체크박스의 상태가 남아야 한다.** 끝낸 일과 남은 일이 종이에서
  똑같이 보이면 그 체크리스트는 인쇄해서 쓸 수 없다.
- 바이트 수와 `%PDF-` 머리만 보지 않는다. **읽어서 확인한다** — 빈 쪽짜리
  PDF 도 그 둘은 통과한다.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib

import pytest
from pypdf import PdfReader

from ieum.core.paper import (
    MAX_CHAPTERS,
    Asset,
    Chapter,
    Paper,
    for_print,
    korean_is_renderable,
    to_docx,
    to_pdf,
)


def _png() -> bytes:
    """1x1 PNG. 그림이 실제로 박히는지 보려면 진짜 파일이어야 한다."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR" + header
    out += struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xFFFFFFFF)
    raw = zlib.compress(b"\x00\xff\x00\x00")
    out += struct.pack(">I", len(raw)) + b"IDAT" + raw
    out += struct.pack(">I", zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF)
    out += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return out


def _text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() for page in reader.pages)


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _paper(body: str, *, title: str = "문서", assets: dict[str, Asset] | None = None) -> Paper:
    return Paper(
        title=title,
        chapters=(Chapter(title=title, body=body),),
        assets=assets or {},
    )


def _docx_paragraphs(data: bytes) -> list[tuple[str | None, str]]:
    """(스타일 이름, 글자) 목록. 글자가 없는 문단은 뺀다."""
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    out: list[tuple[str | None, str]] = []
    for match in re.finditer(r"<w:p\b.*?</w:p>", xml, re.DOTALL):
        block = match.group(0)
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", block))
        if not text:
            continue
        style = re.search(r'w:pStyle w:val="([^"]+)"', block)
        out.append((style.group(1) if style else None, text))
    return out


def test_this_machine_can_draw_korean() -> None:
    """**먼저 이것을 본다.** 아니면 아래 여섯 개가 이유를 숨긴 채 붉어진다.

    한국어 글꼴이 없으면 WeasyPrint 는 터지지 않고 한국어를 텍스트 층에서
    빼 버린 PDF 를 준다. 그러면 아래 시험들이
    `assert '가나다라' in '   '` 로 떨어지는데, 그것만 보고는 조판이 깨진 건지
    글꼴이 없는 건지 알 수 없다. 실제로 CI 에서 그 상태로 여섯 개가 붉었고,
    원인이 글꼴이라는 것을 알아내는 데 오래 걸렸다.

    이 시험이 먼저 붉으면 할 일이 하나다: `fonts-noto-cjk` 를 깐다.
    """
    assert korean_is_renderable(), (
        "이 기계에 한국어를 그릴 수 있는 글꼴이 없다 — `fonts-noto-cjk` 를 설치하세요. "
        "없으면 내보낸 PDF 에서 한국어가 빈칸으로 나갑니다(오류 없이)."
    )


class TestThePdf:
    def test_it_carries_the_words(self) -> None:
        """**읽어서 확인한다.** 바이트 수만 보면 빈 PDF 도 통과한다."""
        pdf = to_pdf(_paper("## 첫 절\n\n한글 본문이 **굵게** 들어간다.\n", title="인쇄 확인"))
        found = _text(pdf)
        assert "인쇄 확인" in found
        assert "첫 절" in found
        assert "한글 본문이" in found

    def test_korean_survives_the_round_trip(self) -> None:
        """한글이 글자로 남아야 한다.

        글꼴이 없으면 WeasyPrint 는 **터지지 않고** 사각형을 그린 멀쩡한 PDF 를
        준다. 뽑아낸 글자가 원문과 같은지 보는 것이 그 자리에서 우리가 할 수
        있는 확인이다 (글꼴 자체는 이미지가 챙긴다 — `apps/api/Dockerfile`).
        """
        pdf = to_pdf(_paper("가나다라 마바사 아자차카 타파하\n", title="한글"))
        assert "가나다라" in _text(pdf)

    def test_each_chapter_starts_a_new_page(self) -> None:
        paper = Paper(
            title="묶음",
            chapters=(
                Chapter(title="첫 문서", body="첫 본문."),
                Chapter(title="둘째 문서", body="둘째 본문."),
            ),
        )
        pdf = to_pdf(paper)
        assert _pages(pdf) == 2

    def test_one_chapter_does_not_start_with_a_blank_page(self) -> None:
        """첫 장에도 쪽 넘김을 걸면 문서 한 부의 첫 쪽이 빈다."""
        assert _pages(to_pdf(_paper("한 줄."))) == 1

    def test_it_numbers_the_pages(self) -> None:
        """흩어진 인쇄물의 순서를 아무도 모르게 두지 않는다."""
        assert "1 / 1" in _text(to_pdf(_paper("한 줄.")))

    def test_the_checkbox_state_survives(self) -> None:
        """**이 시험이 종이 체크리스트를 지킨다.**

        끝낸 일과 남은 일이 똑같이 보이면 인쇄해서 쓸 수 없다.
        """
        found = _text(to_pdf(_paper("- [x] 끝낸 일\n- [ ] 남은 일\n")))
        assert "☑" in found
        assert "☐" in found

    def test_a_live_directive_says_it_is_live(self) -> None:
        """`::chart` 원문이 종이에 박히면 읽는 사람은 그게 뭔지 모른다.
        지우면 자리에 무엇이 있었는지도 사라진다."""
        found = _text(to_pdf(_paper('::chart{query="project = ENG" group=status}\n')))
        assert "chart" in found
        assert "화면에서" in found
        assert "group=status" not in found

    def test_a_table_keeps_its_cells(self) -> None:
        found = _text(to_pdf(_paper("| 가 | 나 |\n|---|---|\n| 1 | 2 |\n")))
        for cell in ("가", "나", "1", "2"):
            assert cell in found

    def test_an_attachment_image_goes_in(self) -> None:
        """**그림이 들어갔는지 직접 본다.**

        처음에는 "그림 있는 문서가 더 크다" 로 썼는데, 두 문서의 글자가 달라
        글꼴 부분집합의 크기도 달라진다 — 그러면 크기 비교가 그림에 대해
        아무것도 말하지 않는다. PDF 안의 이미지를 꺼내 센다.
        """
        pdf = to_pdf(
            _paper(
                "![그림](attachment:abc/a.png)\n",
                assets={"abc/a.png": Asset(_png(), "image/png")},
            )
        )
        assert len(PdfReader(io.BytesIO(pdf)).pages[0].images) == 1

    def test_a_refused_image_is_not_in_the_pdf(self) -> None:
        """거절한 그림이 어떻게든 들어가 있으면 가져왔다는 뜻이다."""
        pdf = to_pdf(_paper("![바깥](https://example.dev/a.png)\n"))
        assert len(PdfReader(io.BytesIO(pdf)).pages[0].images) == 0

    def test_dropped_chapters_are_admitted(self) -> None:
        """**조용히 자르지 않는다.** 자른 인쇄물을 다 그린 인쇄물로 읽으면
        무엇이 빠졌는지 영영 모른다."""
        paper = Paper(title="많다", chapters=(Chapter(title="하나", body="한 줄."),), dropped=3)
        assert "3" in _text(to_pdf(paper))


class TestItDoesNotReachTheNetwork:
    """**이 클래스가 이 기능의 보안이다.**

    조판기가 문서에 적힌 주소를 가져오면, 본문을 쓸 수 있는 사람은 서버의
    자리에서 요청을 보낼 수 있다 — 사내망이든 클라우드 메타데이터든.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "https://example.dev/a.png",
            "file:///etc/passwd",
            "data:image/png;base64,AAAA",
            "//example.dev/a.png",
        ],
    )
    def test_the_fetcher_refuses_everything_but_attachments(self, url: str) -> None:
        from ieum.core.paper import _fetcher

        with pytest.raises(ValueError, match="바깥 주소"):
            _fetcher({})(url)

    def test_the_fetcher_serves_a_packed_attachment(self) -> None:
        from ieum.core.paper import _fetcher

        found = _fetcher({"abc/a.png": Asset(b"bytes", "image/png")})("attachment:abc/a.png")
        assert found["string"] == b"bytes"
        assert found["mime_type"] == "image/png"

    def test_a_missing_attachment_is_refused_too(self) -> None:
        """담기지 않은 첨부는 **가져오지 않는다.** 빈 것을 주면 그림이 안 뜬
        이유가 안 보인다."""
        from ieum.core.paper import _fetcher

        with pytest.raises(ValueError, match="담기지 않은"):
            _fetcher({})("attachment:zzz/none.png")

    def test_a_document_with_an_outside_image_still_exports(self) -> None:
        """**거절이 실패가 되면 안 된다.**

        바깥 그림 하나 때문에 내보내기가 통째로 안 되면 사람은 그 문서를
        영영 못 뽑는다. 그림 자리만 비고 나머지는 나온다.
        """
        body = "앞 글\n\n![메타데이터](http://169.254.169.254/latest/meta-data/)\n\n뒤 글\n"
        found = _text(to_pdf(_paper(body)))
        assert "앞 글" in found
        assert "뒤 글" in found


class TestForPrint:
    def test_internal_links_lose_their_address(self) -> None:
        """종이에서 누를 수 없는 파란 글씨는 읽는 사람을 헷갈리게 한다."""
        html = for_print("[문서로](page:some-slug) 와 [이슈로](issue:ENG-1)\n")
        assert "문서로" in html
        assert "page:some-slug" not in html
        assert "issue:ENG-1" not in html

    def test_outside_links_keep_theirs(self) -> None:
        """바깥 주소는 남긴다 — PDF 에서는 누를 수 있다."""
        html = for_print("[바깥](https://example.dev)\n")
        assert "https://example.dev" in html

    def test_an_unknown_directive_is_left_alone(self) -> None:
        """우리가 모르는 `::` 줄은 사람이 쓴 글일 수 있다. 지우지 않는다."""
        html = for_print("::totallyunknown{a=b}\n")
        assert "totallyunknown" in html
        assert "화면에서" not in html


class TestTheWordFile:
    def test_it_is_a_docx(self) -> None:
        data = to_docx(_paper("한 줄."))
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert "word/document.xml" in names
        assert "[Content_Types].xml" in names

    def test_headings_use_word_styles(self) -> None:
        """스타일을 안 쓰고 글자 크기만 키우면 Word 의 목차·네비게이션이
        문서를 통째로 못 읽는다."""
        found = _docx_paragraphs(to_docx(_paper("## 절 제목\n", title="장 제목")))
        assert ("Heading1", "장 제목") in found
        assert ("Heading3", "절 제목") in found

    def test_a_heading_does_not_carry_explicit_unbold(self) -> None:
        """**직접 서식은 스타일을 이긴다.**

        `run.bold = False` 를 넣으면 python-docx 가 `<w:b w:val="0"/>` 를 쓰고,
        그게 제목 문단에 붙으면 Word 에서 **제목이 굵지 않게** 나온다. 값을
        안 건드려 스타일에서 물려받아야 한다.
        """
        xml = (
            zipfile.ZipFile(io.BytesIO(to_docx(_paper("## 절 제목\n"))))
            .read("word/document.xml")
            .decode()
        )
        assert 'w:val="0"' not in xml

    def test_lists_keep_their_depth(self) -> None:
        found = {
            text: style
            for style, text in _docx_paragraphs(to_docx(_paper("- 하나\n  - 안쪽\n\n1. 첫째\n")))
        }
        assert found["하나"] == "ListBullet"
        assert found["안쪽"] == "ListBullet2"
        assert found["첫째"] == "ListNumber"

    def test_the_checkbox_state_survives(self) -> None:
        """체크박스는 날 HTML 조각으로 오기 때문에 그냥 무시하기 쉽다 —
        그러면 끝낸 일과 남은 일이 Word 에서 똑같이 보인다."""
        found = [
            text for _style, text in _docx_paragraphs(to_docx(_paper("- [x] 끝\n- [ ] 남음\n")))
        ]
        assert any("☑" in text for text in found)
        assert any("☐" in text for text in found)

    def test_a_table_becomes_a_table(self) -> None:
        data = to_docx(_paper("| 가 | 나 |\n|---|---|\n| 1 | 2 |\n"))
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
        assert "<w:tbl>" in xml
        table = re.search(r"<w:tbl>.*?</w:tbl>", xml, re.DOTALL)
        assert table is not None
        assert re.findall(r"<w:t[^>]*>([^<]*)</w:t>", table.group(0)) == ["가", "나", "1", "2"]

    def test_an_attachment_image_goes_in(self) -> None:
        data = to_docx(
            _paper(
                "![그림](attachment:abc/a.png)\n",
                assets={"abc/a.png": Asset(_png(), "image/png")},
            )
        )
        assert any("media/image" in name for name in zipfile.ZipFile(io.BytesIO(data)).namelist())

    def test_a_missing_image_leaves_a_word_behind(self) -> None:
        """조용히 지나가면 읽는 사람은 그 자리에 무엇이 있었는지 모른다."""
        found = [
            text
            for _style, text in _docx_paragraphs(to_docx(_paper("![설명](attachment:zzz/x.png)\n")))
        ]
        assert any("설명" in text for text in found)

    def test_a_live_directive_says_it_is_live(self) -> None:
        found = [text for _style, text in _docx_paragraphs(to_docx(_paper("::toc\n")))]
        assert any("toc" in text and "화면에서" in text for text in found)

    def test_chapters_are_separated_by_page_breaks(self) -> None:
        data = to_docx(
            Paper(
                title="묶음",
                chapters=(Chapter(title="첫", body="가"), Chapter(title="둘", body="나")),
            )
        )
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
        assert 'w:type="page"' in xml


class TestTheChapterLimit:
    def test_it_cuts_and_says_so(self) -> None:
        chapters = tuple(Chapter(title=f"문서 {n}", body="한 줄.") for n in range(MAX_CHAPTERS + 5))
        found = _text(to_pdf(Paper(title="큰 스페이스", chapters=chapters)))
        assert "문서 0" in found
        assert f"문서 {MAX_CHAPTERS + 4}" not in found
        assert "5" in found
