"""설치본이 앱과 **같은 것을 말하는가.**

`deploy/install/` 은 앱 바깥에 있는 파일 셋(compose 하나, 스크립트 하나)인데
앱의 값 몇 개를 **손으로 다시 적는다.** 버킷 이름, 올릴 수 있는 크기, 판 번호.
같은 값을 두 곳에 두면 한 곳만 갱신되는 날이 온다(conventions.md).

이 게이트가 없으면 어떻게 되는가 — 전부 **조용한** 고장이다:

- 버킷 이름이 어긋나면 nginx 가 그 경로를 모르고, 첨부 링크가 SPA 의
  `index.html` 을 200 으로 돌려준다. "업로드가 안 된다" 도 아니고
  "받은 파일이 HTML 이다" 로 나타난다.
- 프록시의 크기 상한이 앱보다 작으면, 앱이 받아 주는 크기를 프록시가 413 으로
  자른다. 앱 쪽 로그에는 아무것도 안 남는다.
- 워커의 헬스체크를 안 덮으면 api 이미지의 것(`/healthz`)을 물려받는데 워커는
  HTTP 를 안 연다. 워커는 영원히 unhealthy 이고 `up --wait` 이 멀쩡한 스택에서
  실패한다 — 실제로 그렇게 실패하는 것을 보고 이 시험을 쓴다.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]

INSTALL_COMPOSE = REPO / "deploy" / "install" / "compose.yml"
INSTALL_SCRIPT = REPO / "deploy" / "install" / "install.sh"
PROD_COMPOSE = REPO / "deploy" / "compose" / "prod.yml"
WEB_NGINX = REPO / "apps" / "web" / "nginx.conf"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _compose(path: Path) -> dict[str, Any]:
    """`${VAR:?...}` 가 들어 있어도 YAML 로는 그냥 문자열이라 그대로 읽힌다."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _size_to_bytes(text: str) -> int:
    """nginx 의 `64m` 같은 표기를 바이트로."""
    match = re.fullmatch(r"(\d+)([kmg]?)", text.strip().lower())
    assert match is not None, f"nginx 크기 표기가 아니다: {text!r}"
    return int(match.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2)]


class TestTheBucketNameIsWrittenTwice:
    """nginx 가 여는 경로와 앱이 쓰는 버킷이 같아야 한다.

    MinIO 는 path-style 이라 presigned URL 의 첫 칸이 곧 버킷 이름이다. 그래서
    경로를 안 고치고 넘길 수 있는 대신, **이름이 갈리면 자리가 안 열린다.**
    """

    def test_the_proxy_opens_the_bucket_the_app_uses(self) -> None:
        from ieum.config import Settings

        default_bucket = Settings.model_fields["s3_bucket"].default
        content = _compose(INSTALL_COMPOSE)["configs"]["attachments-proxy"]["content"]
        assert f"location /{default_bucket}/ " in content, (
            f"프록시가 여는 경로와 앱의 기본 버킷({default_bucket})이 다르다"
        )

    def test_the_compose_pins_that_same_bucket(self) -> None:
        from ieum.config import Settings

        env = _compose(INSTALL_COMPOSE)["x-api-env"]
        assert env["IEUM_S3_BUCKET"] == Settings.model_fields["s3_bucket"].default


class TestTheProxyIsNotNarrowerThanTheApp:
    """프록시가 앱보다 작게 받으면, 앱이 허용하는 크기가 거짓말이 된다."""

    def test_the_api_hop_takes_the_biggest_thing_the_app_accepts(self) -> None:
        from ieum.modules.wiki.portable import MAX_ARCHIVE_BYTES

        text = WEB_NGINX.read_text(encoding="utf-8")
        # `/api/` 블록 안의 값만 본다.
        block = text.split("location /api/ {", 1)[1].split("\n    }", 1)[0]
        found = re.search(r"client_max_body_size\s+(\S+);", block)
        assert found is not None, "`/api/` 에 client_max_body_size 가 없다 (기본 1MB 로 잘린다)"
        assert _size_to_bytes(found.group(1)) >= MAX_ARCHIVE_BYTES

    def test_the_storage_hop_takes_the_biggest_attachment(self) -> None:
        from ieum.core.attachments import MAX_SIZE_BYTES

        content = _compose(INSTALL_COMPOSE)["configs"]["attachments-proxy"]["content"]
        found = re.search(r"client_max_body_size\s+(\S+);", content)
        assert found is not None
        assert _size_to_bytes(found.group(1)) >= MAX_SIZE_BYTES


class TestTheSocketSurvivesTheProxy:
    """동시 편집 소켓은 **운영 이미지의 nginx 를 지나서** 붙는다.

    `collab.ts` 는 `VITE_API_BASE_URL` 이 비면 `window.location.origin` 을 쓰고,
    운영 이미지가 정확히 그 경우다. 업그레이드 헤더가 없으면 핸드셰이크가
    101 이 아닌 것으로 떨어진다 — 빼 보고 확인했다.
    """

    @pytest.mark.parametrize(
        "directive",
        ["proxy_http_version 1.1;", "proxy_set_header Upgrade $http_upgrade;"],
    )
    def test_the_upgrade_headers_are_on_the_api_hop(self, directive: str) -> None:
        text = WEB_NGINX.read_text(encoding="utf-8")
        block = text.split("location /api/ {", 1)[1].split("\n    }", 1)[0]
        assert directive in block

    def test_the_connection_header_has_a_map_behind_it(self) -> None:
        """`Connection: upgrade` 를 늘 보내면 평범한 요청까지 업그레이드로 만든다."""
        text = WEB_NGINX.read_text(encoding="utf-8")
        assert "map $http_upgrade $connection_upgrade" in text
        assert "proxy_set_header Connection $connection_upgrade;" in text


class TestTheWorkerDoesNotInheritTheWrongHealthcheck:
    @pytest.mark.parametrize("path", [INSTALL_COMPOSE, PROD_COMPOSE], ids=["install", "prod"])
    def test_the_worker_says_what_healthy_means_for_a_worker(self, path: Path) -> None:
        worker = _compose(path)["services"]["worker"]
        test = worker.get("healthcheck", {}).get("test")
        assert test is not None, "워커가 api 이미지의 HEALTHCHECK 를 물려받는다 — 늘 붉다"
        assert "--check" in test, f"arq 의 상태를 안 본다: {test}"


class TestTheInstallerOnlyPulls:
    """**빌드하지 않는다** 가 이 설치본의 약속이다.

    빌드 절이 하나라도 들어오면 설치한 사람마다 다른 바이트를 돌리게 되고,
    그때부터 "무엇이 돌고 있냐" 에 답할 수 없다.
    """

    def test_no_service_builds(self) -> None:
        services = _compose(INSTALL_COMPOSE)["services"]
        builders = sorted(name for name, spec in services.items() if "build" in spec)
        assert builders == []

    def test_every_ieum_image_carries_the_same_version(self) -> None:
        """판이 하나라도 고정으로 박히면 그 컨테이너만 딴 판으로 돈다."""
        services = _compose(INSTALL_COMPOSE)["services"]
        ours = {
            name: spec["image"]
            for name, spec in services.items()
            if "/ieum-" in spec.get("image", "")
        }
        assert set(ours) == {"migrate", "seed", "api", "worker", "web"}, sorted(ours)
        assert all("${IEUM_VERSION" in image for image in ours.values()), ours

    def test_only_one_port_is_published(self) -> None:
        """포트가 하나라는 것이 이 설치본이 파는 것이다 — 앞에 붙일 곳이 한 군데."""
        services = _compose(INSTALL_COMPOSE)["services"]
        publishing = {name: spec["ports"] for name, spec in services.items() if spec.get("ports")}
        assert list(publishing) == ["web"], publishing


class TestTheScriptDefaultsToADeclaredVersion:
    def test_the_fallback_version_is_one_we_have_declared(self) -> None:
        """`--version` 없이 쳤을 때 받아 오는 판. 선언한 판이어야 한다 —
        아직 안 낸 판을 기본값으로 두면 처음 깐 사람이 "없는 이미지" 를 만난다."""
        declared = tomllib.loads(
            (REPO / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        text = INSTALL_SCRIPT.read_text(encoding="utf-8")
        found = re.search(r'^VERSION_DEFAULT="([^"]+)"', text, re.M)
        assert found is not None
        assert found.group(1) == declared


class TestTheInstallerPullsWhatTheReleasePushes:
    """**한 방 설치의 전부가 이 한 줄에 걸려 있다.**

    릴리스 워크플로가 올리는 이미지 이름과 설치본이 당기는 이름은 서로 다른
    파일에 손으로 적혀 있다. 어긋나면 판을 내는 날에는 아무도 모르고, **처음
    깐 사람이** `denied` 나 `not found` 를 본다 — 우리 화면에는 안 나타난다.
    """

    def _release_images(self) -> set[str]:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        # `ghcr.io/${{ github.repository_owner }}/ieum-api:${{ ... }}` 에서
        # 이름만 뽑는다. 판과 소유자는 양쪽 다 변수라 견줄 것이 이름이다.
        #
        # 소유자 자리에 **공백이 있다**(`${{ github.repository_owner }}`).
        # 처음에 `\S*?` 로 썼다가 하나도 못 잡았고, 아래 "둘이어야 한다"
        # 시험이 그것을 붙잡았다 — 0개를 0개와 견주면 늘 초록이다.
        return set(re.findall(r"ghcr\.io/.*?/(ieum-[a-z]+):", text))

    def _installer_images(self) -> set[str]:
        services = _compose(INSTALL_COMPOSE)["services"]
        found = set()
        for spec in services.values():
            match = re.search(r"ghcr\.io/.*?/(ieum-[a-z]+):", spec.get("image", ""))
            if match:
                found.add(match.group(1))
        return found

    def test_the_two_lists_are_the_same(self) -> None:
        assert self._release_images() == self._installer_images()

    def test_there_are_two_of_them(self) -> None:
        """0개를 0개와 견주면서 통과하는 것을 막는다."""
        assert self._release_images() == {"ieum-api", "ieum-web"}

    def test_the_owner_is_the_same_on_both_sides(self) -> None:
        """워크플로는 `github.repository_owner`, 설치본은 기본값을 적는다.

        포크한 사람은 `--owner` 로 바꾸지만, **기본값이 우리 소유자여야**
        아무 옵션 없이 친 사람이 우리 이미지를 받는다.
        """
        script = INSTALL_SCRIPT.read_text(encoding="utf-8")
        found = re.search(r'^IMAGE_OWNER_DEFAULT="([^"]+)"', script, re.M)
        assert found is not None
        compose_text = INSTALL_COMPOSE.read_text(encoding="utf-8")
        assert f"IEUM_IMAGE_OWNER:-{found.group(1)}" in compose_text

    def test_the_release_can_run_without_pushing_a_tag(self) -> None:
        """**태그를 밀 수 없는 자리가 있다.** 이 저장소를 다루는 자동화
        환경에서 git 게이트웨이가 태그 ref 를 403 으로 거절한다(브랜치는
        통과한다). 손으로 돌리는 길이 없으면 판을 낼 때마다 사람 하나를
        반드시 거쳐야 한다.
        """
        workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML 이 `on:` 을 불리언 True 로 읽는다 (YAML 1.1).
        triggers = workflow[True] if True in workflow else workflow["on"]
        assert "workflow_dispatch" in triggers
        assert "version" in triggers["workflow_dispatch"]["inputs"]

    def test_the_release_names_itself_from_the_guard_not_the_ref(self) -> None:
        """손으로 돌리면 `github.ref_name` 은 `main` 이다. 그것을 제목으로
        쓰면 릴리스가 `main` 이라는 이름으로 나가고 태그는 안 붙는다."""
        workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
        release_step = workflow["jobs"]["notes"]["steps"][-1]["with"]
        assert release_step["tag_name"] == "${{ needs.guard.outputs.tag }}"
        assert release_step["name"] == "${{ needs.guard.outputs.tag }}"
