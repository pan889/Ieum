"""SAML 2.0 SP (auth.md 4절).

**서명을 실제로 만들고 실제로 검증한다.** 가짜 IdP 가 키를 들고 XML 서명을
붙이므로, 검증을 끄면 이 파일이 붉어진다 — 응답만 그럴듯하게 만들어 두면
검증이 통째로 없어도 통과한다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
import pytest_asyncio

from fake_saml_idp import FakeIdp
from ieum.config import get_settings
from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.modules.identity import saml

pytestmark = pytest.mark.integration

BASE = "/api/v1"


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def sp() -> saml.SpEndpoints:
    return saml.sp_endpoints("http://127.0.0.1:8000")


class TestVerification:
    """검증 코어. DB 없이 돈다."""

    def _verify(self, idp: FakeIdp, sp: saml.SpEndpoints, **kwargs: Any) -> saml.Verified:
        request_id = kwargs.pop("request_id", "_req-1")
        response = idp.response(
            acs_url=sp.acs_url,
            audience=sp.entity_id,
            in_response_to=kwargs.pop("in_response_to", request_id),
            **kwargs,
        )
        return saml.verify_response(
            saml_response=response, idp=idp.config(), sp=sp, request_id=request_id
        )

    def test_a_signed_assertion_passes(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        verified = self._verify(idp, sp)
        assert verified.claims.subject == "saml.person@corp.example.com"
        assert verified.claims.email == "saml.person@corp.example.com"
        assert verified.claims.name == "SAML Person"
        assert verified.claims.groups == ["engineering"]
        # SAML 의 2차 요소 위임은 아직 안 한다. 지어내지 않는다.
        assert verified.claims.mfa_satisfied is False
        assert verified.assertion_id == "_assertion-1"

    def test_an_unsigned_assertion_is_rejected(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        """서명 검증은 **필수**다. 없으면 누구든 어설션을 지어낼 수 있다."""
        with pytest.raises(AuthenticationError) as raised:
            self._verify(idp, sp, signed=False)
        assert raised.value.code == "auth.saml_invalid_response"

    def test_a_signed_response_around_an_unsigned_assertion_is_rejected(
        self, idp: FakeIdp, sp: saml.SpEndpoints
    ) -> None:
        """**서명된 것과 우리가 읽는 것이 같아야 한다.**

        응답 껍데기에만 서명이 붙어 있으면, 그 안의 어설션은 아무나 갈아 끼울
        수 있다. 라이브러리는 "서명이 하나라도 있으면" 통과시키므로, 어설션
        서명을 **요구**하는 설정(`wantAssertionsSigned`)이 이 자리를 막는다.
        """
        with pytest.raises(AuthenticationError) as raised:
            self._verify(idp, sp, sign_response_only=True)
        assert raised.value.code == "auth.saml_invalid_response"

    def test_another_key_is_rejected(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        """등록된 인증서로 검증한다. 다른 키로 서명한 것은 통과하면 안 된다."""
        with pytest.raises(AuthenticationError):
            self._verify(idp, sp, sign_with=FakeIdp().key_pem)

    def test_another_issuer_is_rejected(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        with pytest.raises(AuthenticationError):
            self._verify(idp, sp, issuer="https://evil.example.com/metadata")

    def test_a_response_to_another_request_is_rejected(
        self, idp: FakeIdp, sp: saml.SpEndpoints
    ) -> None:
        """`InResponseTo` 를 안 보면 남이 받은 어설션을 밀어 넣을 수 있다."""
        with pytest.raises(AuthenticationError):
            self._verify(idp, sp, in_response_to="_req-somebody-else")

    def test_an_expired_assertion_is_rejected(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        with pytest.raises(AuthenticationError):
            self._verify(idp, sp, valid_for=timedelta(minutes=-10))

    def test_a_future_assertion_is_rejected(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        """허용 오차는 60초다. 그보다 앞선 것은 아직 유효하지 않다."""
        with pytest.raises(AuthenticationError):
            self._verify(idp, sp, not_before=timedelta(minutes=10))

    def test_the_clock_skew_is_sixty_seconds(self) -> None:
        """auth.md 4절이 정한 값. 넓히면 만료된 어설션이 그만큼 더 오래 통한다."""
        assert saml.CLOCK_SKEW_SECONDS == 60

    def test_another_audience_is_rejected(self, idp: FakeIdp) -> None:
        """우리 EntityID 로 온 것만 받는다. 안 보면 다른 SP 용 어설션이 통한다."""
        other = saml.sp_endpoints("http://elsewhere.example.com")
        response = idp.response(
            acs_url=other.acs_url, audience=other.entity_id, in_response_to="_req-1"
        )
        with pytest.raises(AuthenticationError):
            saml.verify_response(
                saml_response=response,
                idp=idp.config(),
                sp=saml.sp_endpoints("http://127.0.0.1:8000"),
                request_id="_req-1",
            )

    def test_a_missing_name_id_is_refused(self, idp: FakeIdp) -> None:
        with pytest.raises(AuthenticationError) as raised:
            saml.read_attributes(name_id=None, attributes={}, idp=idp.config())
        assert raised.value.code == "auth.saml_name_id_required"

    def test_the_name_id_is_the_email_when_no_attribute_comes(self, idp: FakeIdp) -> None:
        claims = saml.read_attributes(
            name_id="person@corp.example.com", attributes={}, idp=idp.config()
        )
        assert claims.email == "person@corp.example.com"

    def test_groups_stay_empty_without_a_mapping(self, idp: FakeIdp) -> None:
        """설정하지 않은 속성은 읽지 않는다. 지어내면 권한이 늘어난다."""
        claims = saml.read_attributes(
            name_id="person@corp.example.com",
            attributes={"groups": ["engineering"]},
            idp=idp.config(groups_attribute=None),
        )
        assert claims.groups == []


class TestCertificates:
    def test_pem_and_bare_base64_end_up_the_same(self, idp: FakeIdp) -> None:
        """관리자는 IdP 화면에서 복사해 붙인다. 꼴이 여럿이다."""
        pem = idp.certificate_pem
        bare = "".join(pem.strip().splitlines()[1:-1])
        assert saml.normalize_certificate(pem) == saml.normalize_certificate(bare)
        assert saml.normalize_certificate(f"  {bare}  ") == saml.normalize_certificate(bare)

    def test_garbage_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            saml.normalize_certificate("이건 인증서가 아니다")
        assert raised.value.code == "identity.saml_certificate_invalid"

    def test_the_fake_idp_mints_its_certificate_once(self, idp: FakeIdp) -> None:
        """**두 번 읽어도 같은 인증서여야 한다.**

        `@property` 였을 때는 읽을 때마다 새로 발급했고, 유효기간에 `now()` 가
        들어가므로 초가 바뀌면 다른 바이트가 나왔다. 그래서 한 시험 안에서 두
        번 읽으면 이따금 서로 다른 것을 견주게 됐다 — CI 에서 그렇게 붉었다
        (두 값이 `184800Z` 와 `184801Z` 로 1초 달랐다).

        메타데이터 시험만의 문제가 아니었다. `sign()` 이 응답에 박는 인증서와
        `config()` 가 "이게 우리 IdP 다" 로 내놓는 인증서도 서로 다른 것이 될
        수 있었고, 그러면 서명 검증 시험이 이유 없이 붉어진다.

        `is` 로 본다. 값 비교는 초가 안 바뀌면 그냥 통과하므로 아무것도 못
        지킨다 — **같은 객체인가**는 캐시된 것만 만족할 수 있고, 시계와
        상관없이 결정적이다.
        """
        assert idp.certificate_pem is idp.certificate_pem


class TestMetadata:
    def test_reads_issuer_sso_and_certificate(self, idp: FakeIdp) -> None:
        """셋을 손으로 옮겨 적게 하지 않는다."""
        read = saml.read_idp_metadata(idp.metadata_xml())
        assert read.entity_id == idp.entity_id
        assert read.sso_url == idp.sso_url
        assert read.certificates == (saml.normalize_certificate(idp.certificate_pem),)

    def test_incomplete_metadata_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            saml.read_idp_metadata(
                '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"'
                ' entityID="https://idp.example.com"/>'
            )
        assert raised.value.code == "identity.saml_metadata_incomplete"

    def test_our_metadata_names_the_acs(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        xml = saml.metadata_xml(idp.config(), sp)
        assert sp.acs_url in xml
        assert sp.entity_id in xml


class TestPeekIssuer:
    def test_reads_the_issuer_without_verifying(self, idp: FakeIdp, sp: saml.SpEndpoints) -> None:
        response = idp.response(
            acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=None, signed=False
        )
        assert saml.peek_issuer(response) == idp.entity_id

    def test_garbage_gives_nothing(self) -> None:
        assert saml.peek_issuer("not-a-response") is None


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"


async def _step_up(client: httpx.AsyncClient) -> dict[str, str]:
    """step-up 을 통과한 헤더. IdP 등록은 2FA 를 요구한다."""
    signed_in = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

    started = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
    assert started.status_code == 200, started.text
    body = started.json()
    confirmed = await client.post(
        f"{BASE}/auth/mfa/totp/{body['credential_id']}/confirm",
        headers=headers,
        json={"code": pyotp.TOTP(body["secret"]).now()},
    )
    assert confirmed.status_code == 204, confirmed.text
    return headers


@pytest_asyncio.fixture
async def admin(app_client: httpx.AsyncClient) -> dict[str, str]:
    """step-up 을 통과한 관리자 헤더.

    한 테스트에 **한 번만** 만든다. 두 번 부르면 두 번째가 막힌다 — 이미
    확인된 자격증명이 있는 계정은 미완료 세션으로 다시 등록할 수 없다.
    """
    return await _step_up(app_client)


async def _register(
    client: httpx.AsyncClient, idp: FakeIdp, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Corp SAML",
        "metadata_xml": idp.metadata_xml(),
        "groups_attribute": "groups",
        "email_domains": ["corp.example.com"],
    }
    payload.update(overrides)
    created = await client.post(f"{BASE}/admin/sso/saml/providers", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    return dict(created.json())


class TestSamlRegistration:
    async def test_registering_needs_step_up(
        self, app_client: httpx.AsyncClient, idp: FakeIdp
    ) -> None:
        """IdP 설정을 쥐면 **누구로든 로그인할 수 있다.** OIDC 와 같은 문이다."""
        signed_in = await app_client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        refused = await app_client.post(
            f"{BASE}/admin/sso/saml/providers",
            json={"name": "Corp", "metadata_xml": idp.metadata_xml()},
            headers=headers,
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "auth.step_up_requires_mfa"

    async def test_metadata_fills_in_the_rest(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        row = await _register(app_client, idp, admin)
        assert row["kind"] == "saml"
        assert row["issuer"] == idp.entity_id
        assert row["saml_certificate_count"] == 1
        # OIDC 전용 칸은 비어 있다.
        assert row["client_id"] is None

    async def test_the_sp_key_never_comes_back(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """SP 비밀키는 어설션을 여는 열쇠다. 응답에도 목록에도 나오면 안 된다."""
        headers = admin
        created = await app_client.post(
            f"{BASE}/admin/sso/saml/providers",
            json={
                "name": "Corp SAML",
                "metadata_xml": idp.metadata_xml(),
                "sp_private_key": idp.key_pem,
                "sp_certificate": idp.certificate_pem,
                "want_encrypted": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert "PRIVATE KEY" not in created.text
        listed = await app_client.get(f"{BASE}/admin/sso/providers", headers=headers)
        assert "PRIVATE KEY" not in listed.text

    async def test_requiring_encryption_without_a_key_is_refused(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """열 키가 없는데 암호화를 요구하면 **모든 로그인이 실패한다.**"""
        headers = admin
        refused = await app_client.post(
            f"{BASE}/admin/sso/saml/providers",
            json={
                "name": "Corp SAML",
                "metadata_xml": idp.metadata_xml(),
                "want_encrypted": True,
            },
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "identity.saml_sp_key_required"

    async def test_the_same_issuer_twice_is_a_conflict(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        await _register(app_client, idp, admin)
        headers = admin
        again = await app_client.post(
            f"{BASE}/admin/sso/saml/providers",
            json={"name": "Corp SAML (again)", "metadata_xml": idp.metadata_xml()},
            headers=headers,
        )
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "identity.idp_already_registered"

    async def test_the_login_screen_learns_the_kind(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """시작하는 경로가 갈린다. 종류를 안 주면 화면이 둘 중 하나를 찍어야 한다."""
        await _register(app_client, idp, admin)
        public = await app_client.get(f"{BASE}/auth/sso/providers")
        assert public.status_code == 200
        rows = public.json()
        assert rows and rows[0]["kind"] == "saml"
        assert set(rows[0]) == {"id", "name", "kind"}


async def _authn_request_id(client: httpx.AsyncClient, provider_id: str) -> str:
    """로그인을 시작하고 AuthnRequest ID 를 꺼낸다. RelayState 로 돌아온다."""
    started = await client.post(f"{BASE}/auth/saml/{provider_id}/start")
    assert started.status_code == 200, started.text
    url = started.json()["authorization_url"]
    query = parse_qs(urlparse(url).query)
    return query["RelayState"][0]


async def _post_acs(
    client: httpx.AsyncClient, response: str, relay_state: str | None
) -> httpx.Response:
    data = {"SAMLResponse": response}
    if relay_state is not None:
        data["RelayState"] = relay_state
    return await client.post(f"{BASE}/auth/saml/acs", data=data)


class TestSamlLogin:
    """SP-initiated 전체 경로. ACS → 1회용 코드 → 세션."""

    async def test_a_new_person_gets_an_account(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        row = await _register(app_client, idp, admin)
        request_id = await _authn_request_id(app_client, row["id"])
        sp = saml.sp_endpoints(get_settings().public_api_url)

        landed = await _post_acs(
            app_client,
            idp.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=request_id),
            request_id,
        )
        # ACS 는 브라우저를 화면으로 되돌린다. JSON 을 주면 사람이 그걸 본다.
        assert landed.status_code == 303, landed.text
        code = parse_qs(urlparse(landed.headers["location"]).query)["code"][0]
        # **토큰은 주소에 실리지 않는다.** 실으면 브라우저 기록에 남는다.
        assert "access_token" not in landed.headers["location"]

        exchanged = await app_client.post(f"{BASE}/auth/saml/exchange", json={"code": code})
        assert exchanged.status_code == 200, exchanged.text
        tokens = exchanged.json()
        assert tokens["access_token"]

        me = await app_client.get(
            f"{BASE}/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "saml.person@corp.example.com"
        assert me.json()["display_name"] == "SAML Person"

    async def test_the_handoff_code_works_once(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """주소에 실려 가는 값이다. 기록에 남으므로 두 번째는 막아야 한다."""
        row = await _register(app_client, idp, admin)
        request_id = await _authn_request_id(app_client, row["id"])
        sp = saml.sp_endpoints(get_settings().public_api_url)
        landed = await _post_acs(
            app_client,
            idp.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=request_id),
            request_id,
        )
        code = parse_qs(urlparse(landed.headers["location"]).query)["code"][0]

        first = await app_client.post(f"{BASE}/auth/saml/exchange", json={"code": code})
        assert first.status_code == 200, first.text
        again = await app_client.post(f"{BASE}/auth/saml/exchange", json={"code": code})
        assert again.status_code == 401, again.text
        assert again.json()["error"]["code"] == "auth.saml_invalid_handoff"

    async def test_the_same_assertion_cannot_be_replayed(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """라이브러리는 요청 하나만 본다. **재생은 우리가 막는다.**

        어설션은 유효 시간 안에서는 서명이 계속 맞으므로, 한 번 가로챈 응답을
        그 창 안에 다시 밀어 넣으면 그대로 통과한다.
        """
        row = await _register(app_client, idp, admin)
        sp = saml.sp_endpoints(get_settings().public_api_url)

        first_request = await _authn_request_id(app_client, row["id"])
        response = idp.response(
            acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=first_request
        )
        assert (await _post_acs(app_client, response, first_request)).status_code == 303

        # 새 흐름을 열어 같은 어설션을 다시 밀어 넣는다. `InResponseTo` 가
        # 달라 검증에서 걸리므로, 어설션을 그 흐름에 맞춰 다시 만들면서
        # **ID 만 같게** 둔다 — 재생 방지가 ID 를 본다는 뜻이다.
        second_request = await _authn_request_id(app_client, row["id"])
        replay = idp.response(
            acs_url=sp.acs_url,
            audience=sp.entity_id,
            in_response_to=second_request,
            assertion_id="_assertion-1",
        )
        refused = await _post_acs(app_client, replay, second_request)
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.saml_replayed"

    async def test_one_request_takes_one_response(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        row = await _register(app_client, idp, admin)
        request_id = await _authn_request_id(app_client, row["id"])
        sp = saml.sp_endpoints(get_settings().public_api_url)
        response = idp.response(
            acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=request_id
        )
        assert (await _post_acs(app_client, response, request_id)).status_code == 303
        again = await _post_acs(app_client, response, request_id)
        assert again.status_code == 401, again.text
        assert again.json()["error"]["code"] == "auth.saml_replayed"

    async def test_an_unknown_relay_state_is_refused(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        await _register(app_client, idp, admin)
        sp = saml.sp_endpoints(get_settings().public_api_url)
        refused = await _post_acs(
            app_client,
            idp.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to="_made-up"),
            "_made-up",
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.saml_unknown_request"

    async def test_groups_are_synced(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """OIDC 와 같은 자리를 지난다. 여기가 갈리면 한쪽만 고치는 날이 온다."""
        row = await _register(app_client, idp, admin)
        sp = saml.sp_endpoints(get_settings().public_api_url)

        first = await _authn_request_id(app_client, row["id"])
        landed = await _post_acs(
            app_client,
            idp.response(
                acs_url=sp.acs_url,
                audience=sp.entity_id,
                in_response_to=first,
                groups=("engineering", "design"),
            ),
            first,
        )
        code = parse_qs(urlparse(landed.headers["location"]).query)["code"][0]
        await app_client.post(f"{BASE}/auth/saml/exchange", json={"code": code})

        # 두 번째 로그인에서 그룹이 하나로 줄면 **빠져야 한다.**
        second = await _authn_request_id(app_client, row["id"])
        landed = await _post_acs(
            app_client,
            idp.response(
                acs_url=sp.acs_url,
                audience=sp.entity_id,
                in_response_to=second,
                assertion_id="_assertion-2",
                groups=("engineering",),
            ),
            second,
        )
        assert landed.status_code == 303, landed.text

        groups = await app_client.get(f"{BASE}/admin/sso/providers", headers=admin)
        assert groups.status_code == 200


class TestIdpInitiated:
    """IdP 화면에서 시작하는 로그인. 기본은 거절이다."""

    async def test_it_is_refused_by_default(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """`InResponseTo` 가 없으면 우리가 시작한 흐름과 묶을 수 없다 —
        남이 시킨 로그인을 그대로 태우게 된다(로그인 CSRF)."""
        await _register(app_client, idp, admin)
        sp = saml.sp_endpoints(get_settings().public_api_url)
        refused = await _post_acs(
            app_client,
            idp.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=None),
            None,
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.saml_idp_initiated_not_allowed"

    async def test_it_works_when_allowed(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        await _register(app_client, idp, admin, allow_idp_initiated=True)
        sp = saml.sp_endpoints(get_settings().public_api_url)
        landed = await _post_acs(
            app_client,
            idp.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=None),
            None,
        )
        assert landed.status_code == 303, landed.text
        code = parse_qs(urlparse(landed.headers["location"]).query)["code"][0]
        exchanged = await app_client.post(f"{BASE}/auth/saml/exchange", json={"code": code})
        assert exchanged.status_code == 200, exchanged.text

    async def test_an_unknown_issuer_is_refused(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        await _register(app_client, idp, admin, allow_idp_initiated=True)
        sp = saml.sp_endpoints(get_settings().public_api_url)
        refused = await _post_acs(
            app_client,
            idp.response(
                acs_url=sp.acs_url,
                audience=sp.entity_id,
                in_response_to=None,
                issuer="https://evil.example.com/metadata",
            ),
            None,
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.saml_unknown_issuer"


class TestSpMetadata:
    async def test_it_is_open_to_anonymous(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """IdP 쪽 관리자가 우리 계정 없이 가져가야 한다. 안에 든 것은 공개 값뿐이다."""
        row = await _register(app_client, idp, admin)
        fetched = await app_client.get(f"{BASE}/auth/saml/{row['id']}/metadata")
        assert fetched.status_code == 200, fetched.text
        assert "EntityDescriptor" in fetched.text
        assert saml.sp_endpoints(get_settings().public_api_url).acs_url in fetched.text


class TestCertificateRotation:
    """IdP 는 키를 돌린다. 갈아 끼울 길이 없으면 교체하는 날 로그인이 끊긴다."""

    async def test_a_rotated_certificate_takes_over(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        row = await _register(app_client, idp, admin)
        # 같은 발급자, 새 키. 실제 회전이 이렇게 생겼다.
        rotated = FakeIdp(entity_id=idp.entity_id, sso_url=idp.sso_url)
        sp = saml.sp_endpoints(get_settings().public_api_url)

        # 갈아 끼우기 **전에는** 새 키로 서명한 것이 통하지 않는다.
        before = await _authn_request_id(app_client, row["id"])
        refused = await _post_acs(
            app_client,
            rotated.response(acs_url=sp.acs_url, audience=sp.entity_id, in_response_to=before),
            before,
        )
        assert refused.status_code == 401, refused.text

        updated = await app_client.patch(
            f"{BASE}/admin/sso/saml/providers/{row['id']}",
            json={"metadata_xml": rotated.metadata_xml()},
            headers=admin,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["saml_certificate_count"] == 1

        after = await _authn_request_id(app_client, row["id"])
        landed = await _post_acs(
            app_client,
            rotated.response(
                acs_url=sp.acs_url,
                audience=sp.entity_id,
                in_response_to=after,
                assertion_id="_assertion-rotated",
            ),
            after,
        )
        assert landed.status_code == 303, landed.text

    async def test_another_issuer_cannot_take_the_slot(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """발급자가 다르면 다른 IdP 다.

        그 자리에 새 신뢰 기준을 밀어 넣으면 기존 `user_identity` 가 엉뚱한
        IdP 에 묶인다 — 남의 계정으로 들어가는 길이 된다.
        """
        row = await _register(app_client, idp, admin)
        other = FakeIdp(entity_id="https://evil.example.com/metadata")
        refused = await app_client.patch(
            f"{BASE}/admin/sso/saml/providers/{row['id']}",
            json={"metadata_xml": other.metadata_xml()},
            headers=admin,
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "identity.saml_issuer_mismatch"

    async def test_rotation_needs_step_up(
        self, app_client: httpx.AsyncClient, idp: FakeIdp, admin: dict[str, str]
    ) -> None:
        """검증 재료를 바꾸는 일이다. 등록과 같은 문이어야 한다."""
        row = await _register(app_client, idp, admin)
        plain = await app_client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        headers = {"Authorization": f"Bearer {plain.json()['access_token']}"}
        refused = await app_client.patch(
            f"{BASE}/admin/sso/saml/providers/{row['id']}",
            json={"metadata_xml": idp.metadata_xml()},
            headers=headers,
        )
        assert refused.status_code == 403, refused.text
