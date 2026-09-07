"""SSO 공통 (auth.md 4절).

OIDC 와 SAML 이 **같은 것**을 낸다. 프로토콜이 다르니 검증하는 방식은 다르지만,
검증을 통과한 뒤에 하는 일(누구인지 찾고, 없으면 만들고, 그룹을 맞추고, 세션을
연다)은 하나다. 두 벌로 두면 한쪽만 고치는 날이 온다 — 그때 고쳐지지 않은 쪽이
보안 구멍이 된다.

그래서 결과 타입을 여기에 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Claims:
    """IdP 가 준 사람. **여기까지 왔으면 서명·발급자·수신자가 확인됐다.**

    확인되지 않은 값으로 이 타입을 만들지 않는다. 만드는 자리는 프로토콜
    모듈뿐이고, 그 함수들은 검증에 실패하면 예외를 던진다.
    """

    #: IdP 안에서 바뀌지 않는 식별자. OIDC 의 `sub`, SAML 의 `NameID`.
    #: 이메일로 사람을 찾지 않는 이유가 이것이다 — 주소는 바뀐다.
    subject: str
    email: str | None
    name: str | None
    groups: list[str]
    #: IdP 가 2차 요소를 실제로 요구했는가. 위임 정책이 켜져 있을 때만 참이 된다.
    mfa_satisfied: bool
