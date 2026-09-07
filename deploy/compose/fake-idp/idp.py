"""개발·시험용 가짜 OIDC IdP.

**개발 스택에만 있다.** mailpit·MinIO 와 같은 자리의 도구다 — 실제 IdP 없이
SSO 경로 전체(인가 리다이렉트 → 코드 교환 → ID 토큰 검증)를 돌리기 위한
것이고, 그래서 서명 키를 기동할 때마다 새로 만든다. 운영 오버라이드
(deploy/compose/) 에는 들어가지 않는다.

브라우저와 서버가 **다른 주소로** 이 IdP 를 본다. 브라우저는 공개된 포트로
(`localhost:9099`), API 는 컴포즈 네트워크 안에서 서비스 이름으로
(`fake-idp:9099`). `iss` 는 하나여야 하므로 브라우저가 보는 주소로 고정한다 —
MinIO 의 presigned 주소와 같은 문제다.
"""

from __future__ import annotations

import base64
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

PORT = int(os.environ.get("FAKE_IDP_PORT", "9099"))
#: ID 토큰의 `iss`. 브라우저가 보는 주소여야 한다.
ISSUER = os.environ.get("FAKE_IDP_ISSUER", f"http://localhost:{PORT}")
CLIENT_ID = os.environ.get("FAKE_IDP_CLIENT_ID", "ieum-dev")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_numbers = KEY.public_key().public_numbers()


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "fake-1",
            "use": "sig",
            "alg": "RS256",
            "n": _b64(_numbers.n),
            "e": _b64(_numbers.e),
        }
    ]
}

#: 발급한 코드 → (nonce, 사람). 코드는 1회용이다.
PENDING: dict[str, tuple[str, dict[str, Any]]] = {}

#: 로그인할 사람. 쿼리로 갈아 끼울 수 있어 여러 시나리오를 돌린다.
DEFAULT_PERSON = {
    "sub": "fake-person-1",
    "email": "sso.person@corp.example.com",
    "email_verified": True,
    "name": "SSO Person",
    "groups": ["engineering"],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return  # 컴포즈 로그를 요청마다 채우지 않는다

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # 이름은 BaseHTTPRequestHandler 규약이다. 바꾸면 호출되지 않는다.
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/jwks":
            self._send(json.dumps(JWKS).encode())
            return
        if url.path == "/.well-known/openid-configuration":
            self._send(
                json.dumps(
                    {
                        "issuer": ISSUER,
                        "authorization_endpoint": f"{ISSUER}/authorize",
                        "token_endpoint": f"{ISSUER}/token",
                        "jwks_uri": f"{ISSUER}/jwks",
                    }
                ).encode()
            )
            return
        if url.path == "/authorize":
            query = parse_qs(url.query)
            person = dict(DEFAULT_PERSON)
            # 시나리오를 바꿀 수 있게 한다: 다른 사람, 검증 안 된 메일, 다른 그룹.
            for key in ("sub", "email", "name"):
                if key in query:
                    person[key] = query[key][0]
            if "groups" in query:
                person["groups"] = [g for g in query["groups"][0].split(",") if g]
            if query.get("email_verified", [""])[0] == "false":
                person["email_verified"] = False

            code = f"fake-code-{int(time.time() * 1_000_000)}"
            PENDING[code] = (query.get("nonce", [""])[0], person)
            target = f"{query['redirect_uri'][0]}?code={code}&state={query['state'][0]}"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        self._send(b'{"error":"not_found"}', 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        code = form.get("code", [""])[0]
        # 1회용이다. 두 번째는 실패해야 진짜 IdP 처럼 굴러간다.
        if code not in PENDING:
            self._send(b'{"error":"invalid_grant"}', 400)
            return
        nonce, person = PENDING.pop(code)

        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": nonce,
            **person,
        }
        token = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "fake-1"})
        self._send(
            json.dumps({"access_token": "fake", "token_type": "Bearer", "id_token": token}).encode()
        )


if __name__ == "__main__":
    print(f"fake IdP listening on :{PORT}, iss={ISSUER}", flush=True)  # noqa: T201
    # 컴포즈 네트워크 안에서만 뜬다. 브라우저와 api 가 서로 다른 이름으로
    # 오므로 한 인터페이스에 묶을 수 없다. 개발 스택 전용이다.
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104  # nosec B104
