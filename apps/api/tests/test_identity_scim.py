"""SCIM 2.0 의 값 다루기 (RFC 7643·7644).

순수 함수라 값을 손으로 적어 못 박는다. 행을 쓰는 층은
`test_identity_scim_api.py`.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **모르는 것을 조용히 무시하지 않는다.** 못 다루는 필터에 빈 목록을 주면
  IdP 는 "없다" 로 읽고 계정을 다시 만든다 — 같은 사람이 둘이 된다. 못 다루는
  PATCH 경로를 무시하면 "비활성화했는데 계정이 살아 있다" 가 성립한다.
- **경로 없는 PATCH 도 받는다.** Entra 가 그렇게 보낸다. 안 받으면
  비활성화가 통째로 안 된다.
- **`startIndex` 는 1부터다.** 0 을 그대로 빼면 offset 이 음수가 된다.
"""

from __future__ import annotations

import pytest

from ieum.modules.identity.scim import (
    GROUP_FILTERABLE,
    GROUP_PATHS,
    USER_FILTERABLE,
    USER_PATHS,
    Filter,
    PatchOp,
    ScimError,
    page_of,
    parse_filter,
    parse_patch,
    service_provider_config,
    user_resource,
)

PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def patch(*operations: dict[str, object]) -> dict[str, object]:
    return {"schemas": [PATCH_SCHEMA], "Operations": list(operations)}


class TestFilters:
    def test_the_one_shape_we_take(self) -> None:
        found = parse_filter('userName eq "a@b.c"', allowed=USER_FILTERABLE)
        assert found == Filter(attribute="username", value="a@b.c")

    def test_no_filter_is_not_an_error(self) -> None:
        assert parse_filter(None, allowed=USER_FILTERABLE) is None
        assert parse_filter("   ", allowed=USER_FILTERABLE) is None

    def test_it_is_case_insensitive_on_the_attribute(self) -> None:
        # IdP 마다 대소문자가 다르다. 값은 그대로 둔다 — 주소는 값이다.
        found = parse_filter('USERNAME EQ "A@b.c"', allowed=USER_FILTERABLE)
        assert found is not None
        assert found.attribute == "username"
        assert found.value == "A@b.c"

    def test_an_escaped_quote_survives(self) -> None:
        found = parse_filter(r'displayName eq "the \"team\""', allowed=GROUP_FILTERABLE)
        assert found is not None
        assert found.value == 'the "team"'

    def test_a_filter_we_cannot_run_is_refused(self) -> None:
        """**빈 목록을 주지 않는다.** IdP 가 "없다" 로 읽고 계정을 또 만든다."""
        for raw in (
            'userName co "a"',
            "userName pr",
            'userName eq "a" and active eq true',
            "active eq true",
        ):
            with pytest.raises(ScimError) as exc:
                parse_filter(raw, allowed=USER_FILTERABLE)
            assert exc.value.status == 400
            assert exc.value.scim_type == "invalidFilter"

    def test_a_group_cannot_be_filtered_by_username(self) -> None:
        with pytest.raises(ScimError):
            parse_filter('userName eq "a"', allowed=GROUP_FILTERABLE)


class TestPaging:
    def test_start_index_counts_from_one(self) -> None:
        assert page_of(1, 10) == (0, 10)
        assert page_of(11, 10) == (10, 10)

    def test_a_broken_start_index_starts_at_the_beginning(self) -> None:
        # 0 이나 음수를 그대로 빼면 offset 이 음수가 된다.
        assert page_of(0, 10) == (0, 10)
        assert page_of(-5, 10) == (0, 10)
        assert page_of(None, None)[0] == 0

    def test_count_has_a_ceiling(self) -> None:
        assert page_of(1, 10_000)[1] == 200
        assert page_of(1, -1)[1] == 0


class TestPatch:
    def test_replacing_active(self) -> None:
        [op] = parse_patch(
            patch({"op": "replace", "path": "active", "value": False}), allowed=USER_PATHS
        )
        assert op == PatchOp(op="replace", path="active", value=False)

    def test_entra_sends_no_path(self) -> None:
        """`{"op":"replace","value":{"active":false}}` — 스펙이 허용한다."""
        [op] = parse_patch(patch({"op": "replace", "value": {"active": False}}), allowed=USER_PATHS)
        assert op.path == "active"
        assert op.value is False

    def test_a_pathless_op_can_carry_several_attributes(self) -> None:
        ops = parse_patch(
            patch({"op": "replace", "value": {"active": True, "displayName": "새 이름"}}),
            allowed=USER_PATHS,
        )
        assert [o.path for o in ops] == ["active", "displayname"]

    def test_adding_members(self) -> None:
        [op] = parse_patch(
            patch({"op": "add", "path": "members", "value": [{"value": "u-1"}, {"value": "u-2"}]}),
            allowed=GROUP_PATHS,
        )
        assert op.member_ids == ["u-1", "u-2"]

    def test_okta_removes_one_member_by_filter(self) -> None:
        [op] = parse_patch(
            patch({"op": "remove", "path": 'members[value eq "u-1"]'}), allowed=GROUP_PATHS
        )
        assert op.op == "remove"
        assert op.member_ids == ["u-1"]

    def test_a_member_filter_we_do_not_understand_is_refused(self) -> None:
        # 짐작해서 지우면 남의 멤버십이 사라진다.
        with pytest.raises(ScimError) as exc:
            parse_patch(
                patch({"op": "remove", "path": 'members[display eq "team"]'}), allowed=GROUP_PATHS
            )
        assert exc.value.scim_type == "invalidPath"

    def test_a_path_we_do_not_handle_is_refused(self) -> None:
        """무시하면 "비활성화했는데 계정이 살아 있다" 가 조용히 성립한다."""
        with pytest.raises(ScimError) as exc:
            parse_patch(
                patch({"op": "replace", "path": "userType", "value": "guest"}),
                allowed=USER_PATHS,
            )
        assert exc.value.status == 400
        assert exc.value.scim_type == "invalidPath"

    def test_a_group_path_is_not_a_user_path(self) -> None:
        with pytest.raises(ScimError):
            parse_patch(patch({"op": "add", "path": "members"}), allowed=USER_PATHS)

    def test_a_broken_envelope_is_refused(self) -> None:
        for body in (
            {"Operations": [{"op": "replace", "path": "active", "value": False}]},
            {"schemas": [PATCH_SCHEMA]},
            {"schemas": [PATCH_SCHEMA], "Operations": []},
            {"schemas": ["something-else"], "Operations": [{"op": "replace"}]},
            "not-an-object",
        ):
            with pytest.raises(ScimError):
                parse_patch(body, allowed=USER_PATHS)

    def test_an_unknown_op_is_refused(self) -> None:
        with pytest.raises(ScimError):
            parse_patch(patch({"op": "merge", "path": "active", "value": True}), allowed=USER_PATHS)


class TestShapingAResource:
    def test_it_does_not_carry_secrets(self) -> None:
        """SCIM 응답은 IdP 의 로그에 남는다."""
        out = user_resource(
            user_id="u-1",
            user_name="a@b.c",
            display_name="가나",
            active=True,
            external_id=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            location="/scim/v2/Users/u-1",
        )
        flat = repr(out).lower()
        for secret in ("password", "hash", "token", "secret", "session"):
            assert secret not in flat

    def test_an_absent_external_id_is_absent(self) -> None:
        # `null` 로 두면 IdP 가 "빈 값으로 설정" 으로 읽고 자기 쪽을 지운다.
        out = user_resource(
            user_id="u-1",
            user_name="a@b.c",
            display_name="가나",
            active=True,
            external_id=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            location="/scim/v2/Users/u-1",
        )
        assert "externalId" not in out


class TestSayingWhatWeSupport:
    def test_it_does_not_promise_what_we_refuse(self) -> None:
        """정직하지 않으면 IdP 는 맞게 부른 요청이 실패하는 것을 본다."""
        config = service_provider_config("/scim/v2/ServiceProviderConfig")
        assert config["bulk"]["supported"] is False
        assert config["sort"]["supported"] is False
        assert config["changePassword"]["supported"] is False
        assert config["patch"]["supported"] is True
