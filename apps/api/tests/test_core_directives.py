"""디렉티브(매크로) 문법과 검사 (wiki-markdown.md 3절).

두 가지를 고정한다.

1. **모르는 이름은 그냥 텍스트다.** 남의 도구가 만든 문서에 `::` 로 시작하는
   줄이 있다고 문서 전체를 거절하면 임포트가 쓸모없어진다.
2. **아는 이름은 인자까지 본다.** 지원한다고 말한 것에 대해서만 책임진다.
   저장 시점에 거절해야 한다 — 보는 시점에 처음 알면 쓴 사람은 이미 없다.
"""

from __future__ import annotations

import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.markdown import normalize
from ieum.core.markdown.directives import (
    Directive,
    parse_attrs,
    parse_leaf,
    validate,
    validate_source,
)


class TestParseAttrs:
    def test_reads_bare_and_quoted(self) -> None:
        assert parse_attrs('depth=3 query="a b c"') == {"depth": "3", "query": "a b c"}

    def test_a_key_without_a_value_is_empty(self) -> None:
        assert parse_attrs("compact") == {"compact": ""}

    def test_keys_are_lowercased(self) -> None:
        assert parse_attrs("Depth=2") == {"depth": "2"}

    def test_quoted_values_may_hold_spaces_and_operators(self) -> None:
        # IQL 이 그대로 들어온다. 공백에서 끊기면 질의를 쓸 수 없다.
        attrs = parse_attrs('query="project = ENG AND status != Done"')
        assert attrs["query"] == "project = ENG AND status != Done"

    def test_empty_is_empty(self) -> None:
        assert parse_attrs("") == {}


class TestParseLeaf:
    @pytest.mark.parametrize(
        ("line", "name"),
        [("::toc", "toc"), ("::toc{depth=2}", "toc"), ("::children  ", "children")],
    )
    def test_reads_a_directive_line(self, line: str, name: str) -> None:
        parsed = parse_leaf(line)
        assert parsed is not None
        assert parsed.name == name

    @pytest.mark.parametrize(
        "line",
        [
            "::toc 뒤에 글자",
            "앞에 글자 ::toc",
            ":toc",
            "::",
            "  ::toc",  # 들여쓰기하면 문단이다
        ],
    )
    def test_rejects_lines_that_are_not_directives(self, line: str) -> None:
        assert parse_leaf(line) is None


class TestValidate:
    @pytest.mark.parametrize(
        "source",
        [
            "::unknown",
            "::unknown{whatever=1}",
            '::gallery{album="2026"}',  # 아직 없는 이름
        ],
    )
    def test_unknown_names_pass_through(self, source: str) -> None:
        """거절하면 남의 문서를 못 가져온다. 화면에는 텍스트로 남는다."""
        validate_source(source)

    @pytest.mark.parametrize("source", ["::toc", "::toc{depth=1}", "::toc{depth=6}"])
    def test_accepts_valid_toc(self, source: str) -> None:
        validate_source(source)

    @pytest.mark.parametrize(
        "source",
        [
            '::excerpt{page="ENG/deploy"}',
            '::excerpt{page="ENG/deploy/rollback"}',
            '::excerpt{page="ENG"}',
        ],
    )
    def test_accepts_valid_excerpt(self, source: str) -> None:
        validate_source(source)

    @pytest.mark.parametrize(
        ("source", "code"),
        [
            ("::excerpt", "markdown.directive_needs_page"),
            ('::excerpt{page=""}', "markdown.directive_needs_page"),
            ('::excerpt{page="/leading"}', "markdown.invalid_page_path"),
            ('::excerpt{page="ENG/"}', "markdown.invalid_page_path"),
            ('::excerpt{pages="ENG/x"}', "markdown.unknown_directive_arg"),
        ],
    )
    def test_rejects_bad_excerpt(self, source: str, code: str) -> None:
        """아는 이름은 인자까지 엄격하게 본다. 저장 시점에 말해야 쓴 사람이 고친다."""
        with pytest.raises(ValidationError) as exc:
            validate_source(source)
        assert exc.value.code == code

    @pytest.mark.parametrize(
        ("source", "code"),
        [
            ("::toc{depth=0}", "markdown.invalid_directive_depth"),
            ("::toc{depth=7}", "markdown.invalid_directive_depth"),
            ("::toc{depth=x}", "markdown.invalid_directive_depth"),
            ("::toc{deep=3}", "markdown.unknown_directive_arg"),
            ("::children{page=a depth=99}", "markdown.invalid_directive_depth"),
            ("::issues", "markdown.directive_needs_query"),
            ('::issues{query=""}', "markdown.directive_needs_query"),
            ('::issues{query="x" columns="key,nope"}', "markdown.unknown_issue_column"),
            ('::issues{query="x" limit=0}', "markdown.invalid_directive_limit"),
            ('::issues{query="x" limit=101}', "markdown.invalid_directive_limit"),
            ('::issues{query="x" order="key"}', "markdown.unknown_directive_arg"),
            ("::chart", "markdown.directive_needs_query"),
            ("::chart{group=status}", "markdown.directive_needs_query"),
            ('::chart{query="x"}', "markdown.directive_needs_group"),
            ('::chart{query="x" group=""}', "markdown.directive_needs_group"),
            ('::chart{query="x" group=status limit=0}', "markdown.invalid_directive_limit"),
            ('::chart{query="x" group=status limit=51}', "markdown.invalid_directive_limit"),
            ('::chart{query="x" group=status limit=x}', "markdown.invalid_directive_limit"),
            ('::chart{query="x" group=status kind=pie}', "markdown.unknown_directive_arg"),
        ],
    )
    def test_rejects_bad_arguments(self, source: str, code: str) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_source(source)
        assert exc.value.code == code

    def test_the_error_names_the_directive(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_source("::toc{depth=9}")
        assert exc.value.details["directive"] == "toc"

    @pytest.mark.parametrize(
        "source",
        [
            '::chart{query="project = OPS" group=status}',
            '::chart{query="project = OPS" group=assignee limit=5}',
            '::chart{query="project = OPS" group=labels limit=50}',
        ],
    )
    def test_accepts_valid_chart(self, source: str) -> None:
        validate_source(source)

    def test_it_does_not_judge_the_group_name(self) -> None:
        """**셀 수 있는 기준은 커널이 모른다** (모듈 경계).

        `reports.GROUPS` 는 이슈 모듈 것이고 여기는 커널이다. `::issues` 의
        IQL 과 같은 처지라 같게 다룬다: 저장은 통과하고, 이름이 틀린 것은
        보는 시점에 리포트 API 가 말한다. 여기서 목록을 베껴 두면 두 벌이
        되고, 기준이 하나 늘 때 문서 저장만 조용히 막힌다.
        """
        validate_source('::chart{query="x" group=nope}')

    def test_containers_take_only_a_title(self) -> None:
        validate(Directive(name="info", attrs={"title": "읽어 주세요"}, body=""))
        with pytest.raises(ValidationError):
            validate(Directive(name="info", attrs={"color": "red"}, body=""))

    def test_an_unknown_container_passes_through(self) -> None:
        validate(Directive(name="collapse", attrs={"anything": "1"}, body=""))


class TestValidateSource:
    def test_code_blocks_are_examples_not_directives(self) -> None:
        """문서에서 문법을 설명하려면 코드 블록에 써야 한다."""
        validate_source("설명:\n\n```\n::toc{depth=99}\n```\n")

    def test_finds_a_directive_after_a_code_block(self) -> None:
        with pytest.raises(ValidationError):
            validate_source("```\nx\n```\n\n::toc{depth=99}\n")

    def test_checks_container_open_lines(self) -> None:
        with pytest.raises(ValidationError):
            validate_source(":::info{color=red}\n본문\n:::")


class TestNormalizeKeepsDirectives:
    """정규화가 디렉티브를 뭉개면 문서가 저장될 때마다 매크로가 사라진다."""

    @pytest.mark.parametrize(
        "source",
        [
            "::toc{depth=3}",
            '::issues{query="project = ENG AND status != Done" columns="key,summary"}',
            "앞\n\n::children{depth=2}\n\n뒤",
        ],
    )
    def test_leaf_survives(self, source: str) -> None:
        assert normalize(source) == source

    def test_chart_survives(self) -> None:
        source = '::chart{query="project = OPS" group=status limit=5}'
        assert normalize(source) == source

    def test_container_survives_with_a_list_inside(self) -> None:
        """닫는 `:::` 이 마지막 목록 항목으로 빨려 들어가면 안 된다.

        멱등하기까지 해서 멱등성 테스트로는 안 잡혔다 — 두 번 돌려도 같은
        (망가진) 결과가 나온다.
        """
        source = ":::warning\n- 하나\n- 둘\n:::"
        assert normalize(source) == source

    def test_container_content_is_normalized(self) -> None:
        assert normalize(":::info\n안쪽   공백\n:::") == ":::info\n안쪽 공백\n:::"

    def test_nesting_needs_a_longer_fence(self) -> None:
        source = "::::info\n:::note\n안\n:::\n::::"
        assert normalize(source) == source

    def test_a_colon_run_inside_widens_the_fence(self) -> None:
        # 안 넓히면 본문의 `:::` 에서 상자가 끊긴다.
        assert normalize(":::info\n본문에 ::: 이 있다\n:::") == "::::info\n본문에 ::: 이 있다\n::::"

    @pytest.mark.parametrize(
        "source",
        [
            ":::info\n상자\n:::",
            ":::warning\n- 하나\n- 둘\n:::",
            "::toc{depth=2}\n\n:::tip\n### 제목\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n:::",
        ],
    )
    def test_idempotent(self, source: str) -> None:
        once = normalize(source)
        assert normalize(once) == once
