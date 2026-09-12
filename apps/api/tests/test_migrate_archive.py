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
    MAX_LABEL,
    MAX_PROGRESS,
    MAX_SUMMARY,
    MIN_PROGRESS,
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


class TestItFitsValuesToWhatTheOtherSideCanHold:
    """소스마다 상한이 다르다. **읽는 자리에서** 맞춘다.

    안 맞추면 이슈를 만드는 도중에 DB 가 거절하고, 그때는 이미 앞의 수백 건이
    들어간 뒤다 — 절반만 옮겨진 프로젝트가 남는다. 그리고 미리 보기는 그 사고를
    예고하지 못한다(같은 읽기를 쓰므로 여기서 맞춰야 둘이 같은 것을 본다).

    **조용히 자르지는 않는다.** 자른 것은 `trimmed` 로 나오고 적재 보고의
    "안 옮겨진 것" 에 그대로 실린다.
    """

    @staticmethod
    def _one(**overrides: object) -> Issue:
        base = {"source_id": "7", "summary": "제목"}
        return read_archive(
            write_archive(_replace_issues([Issue(**{**base, **overrides})]))
        ).issues[0]

    def test_a_long_summary_is_cut_and_reported(self) -> None:
        issue = self._one(summary="가" * (MAX_SUMMARY + 200))
        assert len(issue.summary) == MAX_SUMMARY
        assert any("제목이 길어" in line for line in issue.trimmed)

    def test_a_summary_that_fits_is_left_alone(self) -> None:
        issue = self._one(summary="가" * MAX_SUMMARY)
        assert len(issue.summary) == MAX_SUMMARY
        assert issue.trimmed == ()

    @pytest.mark.parametrize(("given", "expected"), [(-30, 0), (500, 100), (0, 0), (100, 100)])
    def test_progress_lands_inside_the_allowed_range(self, given: int, expected: int) -> None:
        issue = self._one(done_ratio=given)
        assert issue.done_ratio == expected
        assert bool(issue.trimmed) is (given != expected)

    def test_a_label_too_long_is_dropped_not_cut(self) -> None:
        """라벨은 글자가 곧 이름이다. 자르면 **다른 라벨**이 된다."""
        issue = self._one(labels=["짧은것", "나" * (MAX_LABEL + 1)])
        assert issue.labels == ["짧은것"]
        assert any("라벨이 길어" in line for line in issue.trimmed)


class TestACommentNeedsItsOwnId:
    """빈 `source_id` 는 "이미 옮겼나" 를 가릴 수 없게 만든다.

    그리고 지금 적재는 그런 코멘트를 **첫 하나만 넣고 나머지를 이미 옮긴 것으로
    보고 버린다.** 조용히 사라지는 쪽이 거절보다 나쁘므로 읽는 자리에서 막는다.
    """

    def test_a_blank_one_is_refused(self) -> None:
        archive = _replace_issues(
            [
                Issue(
                    source_id="7",
                    summary="제목",
                    comments=[
                        Comment(source_id="", body="첫 줄", created_at="", author=""),
                        Comment(source_id="", body="둘째 줄", created_at="", author=""),
                    ],
                )
            ]
        )
        with pytest.raises(ArchiveError, match="코멘트의 `source_id`"):
            read_archive(write_archive(archive))

    def test_a_real_one_goes_through(self) -> None:
        archive = _replace_issues(
            [
                Issue(
                    source_id="7",
                    summary="제목",
                    comments=[Comment(source_id="c1", body="한 줄", created_at="", author="")],
                )
            ]
        )
        assert read_archive(write_archive(archive)).issues[0].comments[0].source_id == "c1"


class TestTheLimitsMatchTheColumns:
    """여기 상수가 받는 쪽 열보다 크면 **아무것도 못 막는다.**

    맞대어 보지 않으면 언젠가 갈라진다 — 열을 줄이는 쪽은 모델만 고치고,
    이관은 그때부터 조용히 절반씩 들어간다.
    """

    def test_summary_and_label_match(self) -> None:
        from ieum.modules.issues.models import Issue as IssueRow
        from ieum.modules.issues.models import IssueLabel

        assert IssueRow.__table__.c.summary.type.length == MAX_SUMMARY
        assert IssueLabel.__table__.c.label.type.length == MAX_LABEL

    def test_the_progress_range_matches_the_check_constraint(self) -> None:
        from ieum.modules.issues.models import Issue as IssueRow

        # 이름은 규약이 앞에 `ck_<표>_` 를 붙인다. 이름을 통째로 적으면 규약이
        # 바뀔 때 이 시험이 먼저 붉어진다 — 여기서 지킬 것은 이름이 아니라 범위다.
        checks = [
            str(c.sqltext)
            for c in IssueRow.__table__.constraints
            if "progress_range" in (getattr(c, "name", "") or "")
        ]
        assert checks, "progress 범위 제약이 사라졌다"
        assert f"BETWEEN {MIN_PROGRESS} AND {MAX_PROGRESS}" in checks[0]


def _replace_issues(issues: list[Issue]) -> Archive:
    base = _archive()
    return Archive(manifest=base.manifest, people=base.people, issues=issues)
