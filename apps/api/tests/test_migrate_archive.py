"""이관 묶음 포맷.

**여기가 이 기능의 계약이다.** 묶음은 관리자의 기계에서 만들어져 며칠 뒤
올라올 수 있고(ADR-0016), 그 사이에 우리 판이 올라가 있을 수 있다. 그래서
읽기 쪽은 두 가지를 다 해야 한다 — 옛 묶음을 계속 읽고, **모르는 것은 조용히
넘기지 않고 어디가 문제인지 말하고 멈추는 것.**
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from ieum.migrate.archive import (
    FORMAT_VERSION,
    ISSUES_NAME,
    MANIFEST_NAME,
    PEOPLE_NAME,
    Archive,
    ArchiveError,
    Comment,
    Issue,
    Manifest,
    Person,
    Project,
    Relation,
    Source,
    read_archive,
    write_archive,
)


def _archive() -> Archive:
    return Archive(
        manifest=Manifest(
            source=Source(kind="redmine", base_url="http://redmine.example", version="6.0"),
            project=Project(key="migrate-probe", name="이관 시험 프로젝트", description="설명"),
            taken_at="2026-09-11T10:40:00Z",
            adapter="ieum-migrate/1",
        ),
        people=[
            Person(source_id="5", name="하나 김", email="hana@example.com", login="hana"),
            # 메일이 없는 사람. **이 줄이 이 시험의 요점 중 하나다** — 잇지 못할
            # 사람도 묶음에는 들어와야 받는 쪽이 보고서에 적을 수 있다.
            Person(source_id="9", name="이름만 있는 사람"),
        ],
        issues=[
            Issue(
                source_id="2",
                summary="로그인이 가끔 실패한다",
                description="본문에 **마크다운**이 그대로.",
                type="Bug",
                status="New",
                priority="High",
                author="1",
                assignee="5",
                created_at="2026-09-11T10:37:56Z",
                done_ratio=0,
                relations=[Relation(kind="relates", target="3")],
                comments=[
                    Comment(
                        source_id="1",
                        body="재현했다.",
                        created_at="2026-09-11T10:38:00Z",
                        author="1",
                    )
                ],
            ),
            Issue(source_id="3", summary="정렬을 바꾸고 싶다", parent="2", labels=["ux"]),
        ],
    )


class TestRoundTrip:
    def test_what_goes_in_comes_out(self) -> None:
        back = read_archive(write_archive(_archive()))
        assert back == _archive()

    def test_zero_is_not_dropped(self) -> None:
        """`done_ratio: 0` 은 **뜻이 있는 값**이다 — 빈 것으로 보고 빼면 안 된다."""
        data = write_archive(_archive())
        with zipfile.ZipFile(BytesIO(data)) as zf:
            first = json.loads(zf.read(ISSUES_NAME).splitlines()[0])
        assert first["done_ratio"] == 0

    def test_the_manifest_counts_before_anyone_reads_it(self) -> None:
        """미리 보기가 규모를 먼저 보여 줄 수 있어야 한다."""
        data = write_archive(_archive())
        with zipfile.ZipFile(BytesIO(data)) as zf:
            manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["counts"] == {"people": 2, "issues": 2, "comments": 1}

    def test_it_is_line_oriented(self) -> None:
        """한 줄에 하나. 오만 개짜리를 `head` 로 들여다볼 수 있어야 한다."""
        data = write_archive(_archive())
        with zipfile.ZipFile(BytesIO(data)) as zf:
            assert len(zf.read(ISSUES_NAME).splitlines()) == 2
            assert len(zf.read(PEOPLE_NAME).splitlines()) == 2

    def test_an_empty_project_is_still_a_project(self) -> None:
        empty = Archive(manifest=_archive().manifest, people=[], issues=[])
        assert read_archive(write_archive(empty)) == empty


def _tamper(files: dict[str, str]) -> bytes:
    base = _archive()
    data = write_archive(base)
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, files.get(name, src.read(name)))
    return out.getvalue()


class TestItSaysWhereItBroke:
    """메시지는 **고칠 수 있게** 쓴다. 어느 파일 몇 번째 줄인지까지."""

    def test_not_a_zip(self) -> None:
        with pytest.raises(ArchiveError, match="ZIP"):
            read_archive(b"not a zip at all")

    def test_missing_member(self) -> None:
        out = BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr(MANIFEST_NAME, "{}")
        with pytest.raises(ArchiveError, match=PEOPLE_NAME):
            read_archive(out.getvalue())

    def test_a_broken_line_names_its_number(self) -> None:
        lines = "\n".join(
            [
                json.dumps({"source_id": "2", "summary": "괜찮은 줄"}),
                "{이건 JSON 이 아니다",
            ]
        )
        with pytest.raises(ArchiveError, match=rf"{ISSUES_NAME}:2"):
            read_archive(_tamper({ISSUES_NAME: lines}))

    def test_a_missing_summary_names_its_line(self) -> None:
        lines = json.dumps({"source_id": "2", "summary": ""})
        with pytest.raises(ArchiveError, match=rf"{ISSUES_NAME}:1.*summary"):
            read_archive(_tamper({ISSUES_NAME: lines}))

    def test_duplicate_source_ids_are_refused(self) -> None:
        """(출처, 원래 id) 가 멱등의 열쇠다. 겹치면 그 열쇠가 열쇠가 아니다."""
        lines = "\n".join(json.dumps({"source_id": "2", "summary": f"{n}번"}) for n in (1, 2))
        with pytest.raises(ArchiveError, match="겹친다"):
            read_archive(_tamper({ISSUES_NAME: lines}))

    def test_a_wrong_type_is_not_coerced(self) -> None:
        """`done_ratio: "50"` 을 50 으로 읽어 주지 않는다 — 조용한 추측이 제일 나쁘다."""
        lines = json.dumps({"source_id": "2", "summary": "제목", "done_ratio": "50"})
        with pytest.raises(ArchiveError, match="done_ratio"):
            read_archive(_tamper({ISSUES_NAME: lines}))

    def test_true_is_not_an_integer(self) -> None:
        """파이썬에서 `True` 는 `int` 다. 그 구멍으로 들어오지 못하게 한다."""
        lines = json.dumps({"source_id": "2", "summary": "제목", "done_ratio": True})
        with pytest.raises(ArchiveError, match="done_ratio"):
            read_archive(_tamper({ISSUES_NAME: lines}))


class TestTheFormatVersion:
    def test_a_newer_archive_is_refused_loudly(self) -> None:
        """**모르는 판을 읽어 주지 않는다.** 모르는 필드를 조용히 버리면
        사람은 다 들어온 줄 알고, 그 사실을 몇 달 뒤에 안다."""
        manifest = json.loads(_tamper_manifest())
        manifest["format"] = FORMAT_VERSION + 1
        with pytest.raises(ArchiveError, match=f"{FORMAT_VERSION} 까지"):
            read_archive(_tamper({MANIFEST_NAME: json.dumps(manifest)}))

    def test_something_that_is_not_an_archive_says_so(self) -> None:
        with pytest.raises(ArchiveError, match="이관 묶음이 아닌"):
            read_archive(_tamper({MANIFEST_NAME: json.dumps({"hello": "world"})}))


def _tamper_manifest() -> str:
    with zipfile.ZipFile(BytesIO(write_archive(_archive()))) as zf:
        return zf.read(MANIFEST_NAME).decode()


class TestLimits:
    def test_a_zip_bomb_is_refused_before_it_is_read(self) -> None:
        """작은 ZIP 이 기가바이트로 부푸는 것을 **열기 전에** 막는다."""
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME, "{}")
            zf.writestr(PEOPLE_NAME, "")
            zf.writestr(ISSUES_NAME, "0" * (300 * 1024 * 1024))
        with pytest.raises(ArchiveError, match="압축을 풀면"):
            read_archive(out.getvalue())


class TestUnknownRelations:
    def test_they_come_through_instead_of_killing_the_import(self) -> None:
        """소스마다 관계 어휘가 다르다. 여기서 거절하면 **묶음 하나가 통째로**
        안 들어온다 — 옮길 수 없는 것은 받는 쪽이 보고서에 남긴다."""
        lines = json.dumps(
            {
                "source_id": "2",
                "summary": "제목",
                "relations": [{"kind": "따라한다", "target": "3"}],
            }
        )
        back = read_archive(_tamper({ISSUES_NAME: lines}))
        assert back.issues[0].relations == [Relation(kind="따라한다", target="3")]
