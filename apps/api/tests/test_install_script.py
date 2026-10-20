"""`install.sh` 를 **실제로 돌려 본다** — docker·curl 은 가짜로 세운다.

이 스크립트의 고장은 읽어서는 잘 안 보이는 자리에 있었다:

- **안내대로 판을 올리면 compose.yml 이 안 바뀌었다.** 끝에 찍어 주는
  `cd <설치 자리> && ./install.sh --version X` 는 스크립트가 설치 자리에서 도는
  경우인데, 그때 옆의 compose.yml 이 곧 설치본의 것이라 "이미 이 자리에 있다"
  로 지나갔다. 이미지만 새 판이 되고 설정은 옛 판으로 남았다.
- **무작위 값을 못 만들어도 설치가 계속됐다.** `$(rand | cut …)` 과 heredoc
  안의 `$(rand)` 는 rand 의 `die` 가 서브셸만 끝내서, 빈 비밀 키와 빈 관리자
  비밀번호가 `.env` 에 적혔다.

둘 다 `sh`(우분투에서는 dash)와 `bash` 에서 본다. 설치 안내가 `sh install.sh`
라서 dash 가 실제로 도는 셸이다.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_DIR = REPO / "deploy" / "install"

SHELLS = [name for name in ("dash", "bash") if shutil.which(name)]

#: 가짜 docker. 무엇을 물어도 된다고 답한다 — 여기서 보는 것은 파일이다.
FAKE_DOCKER = """#!/bin/sh
case "$*" in
    *"--format"*) echo 0.0.0 ;;
    *"--short"*) echo 0.0.0 ;;
esac
exit 0
"""

#: 가짜 curl. `-o` 로 받을 자리를 받고, URL 을 `$FAKE_REMOTE` 아래 파일로 푼다.
#: 부른 URL 을 적어 둔다 — 무엇을 받으러 갔는지가 시험의 요점이다.
FAKE_CURL = """#!/bin/sh
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$FAKE_REMOTE/requests.log"
path="$FAKE_REMOTE/${url#https://raw.githubusercontent.com/pan889/Ieum/}"
[ -f "$path" ] || exit 22
cp "$path" "$out"
"""

#: 무작위 값을 못 만드는 openssl.
BROKEN_OPENSSL = """#!/bin/sh
exit 1
"""


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "docker", FAKE_DOCKER)
    _executable(bin_dir / "curl", FAKE_CURL)

    # 원격: 두 판을 놓는다. 새 판은 알아볼 수 있게 표시를 붙인다.
    remote = tmp_path / "remote"
    for version, mark in (("1.2.1", ""), ("9.9.9", "# 판 9.9.9 의 것\n")):
        dest = remote / f"v{version}" / "deploy" / "install"
        dest.mkdir(parents=True)
        for name in ("compose.yml", "install.sh"):
            text = (INSTALL_DIR / name).read_text(encoding="utf-8")
            (dest / name).write_text(text + mark, encoding="utf-8")

    site = tmp_path / "site"
    site.mkdir()
    return {"bin": bin_dir, "remote": remote, "site": site}


def _run(
    shell: str, sandbox: dict[str, Path], *args: str, script: str = "install.sh"
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{sandbox['bin']}{os.pathsep}{os.environ['PATH']}",
        "FAKE_REMOTE": str(sandbox["remote"]),
    }
    return subprocess.run(  # noqa: S603 - 인자가 전부 우리가 적은 것이다
        [shutil.which(shell) or shell, script, *args],
        cwd=sandbox["site"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _env(site: Path) -> dict[str, str]:
    pairs = {}
    for line in (site / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


@pytest.mark.parametrize("shell", SHELLS)
class TestUpgradingInPlace:
    def _install(self, shell: str, sandbox: dict[str, Path]) -> None:
        # 안내 그대로다: 스크립트를 받아 그 자리에서 돌린다.
        shutil.copy(INSTALL_DIR / "install.sh", sandbox["site"] / "install.sh")
        done = _run(shell, sandbox, "--url", "http://localhost:8080", "--yes")
        assert done.returncode == 0, done.stderr

    def test_the_upgrade_replaces_compose_and_itself(
        self, shell: str, sandbox: dict[str, Path]
    ) -> None:
        self._install(shell, sandbox)
        site = sandbox["site"]
        assert "판 9.9.9" not in (site / "compose.yml").read_text(encoding="utf-8")

        done = _run(shell, sandbox, "--version", "9.9.9", script="./install.sh")
        assert done.returncode == 0, done.stderr

        requests = (sandbox["remote"] / "requests.log").read_text(encoding="utf-8")
        assert "/v9.9.9/deploy/install/compose.yml" in requests, requests
        assert "/v9.9.9/deploy/install/install.sh" in requests, requests
        assert "판 9.9.9" in (site / "compose.yml").read_text(encoding="utf-8")
        assert "판 9.9.9" in (site / "install.sh").read_text(encoding="utf-8")
        assert os.access(site / "install.sh", os.X_OK)
        assert _env(site)["IEUM_VERSION"] == "9.9.9"

    def test_a_failed_download_leaves_the_version_alone(
        self, shell: str, sandbox: dict[str, Path]
    ) -> None:
        """받지 못했는데 `.env` 의 판만 바뀌면, 다음 `up` 이 옛 compose.yml 로
        새 이미지를 띄운다. 그리고 다시 친 판올림은 "이미 그 판이다" 로 보인다."""
        self._install(shell, sandbox)
        site = sandbox["site"]
        before = (site / "compose.yml").read_text(encoding="utf-8")

        done = _run(shell, sandbox, "--version", "8.8.8", script="./install.sh")
        assert done.returncode != 0
        assert _env(site)["IEUM_VERSION"] == "1.2.1"
        assert (site / "compose.yml").read_text(encoding="utf-8") == before
        assert not list(site.glob("*.new.*")), "반쪽짜리 파일이 남았다"


@pytest.mark.parametrize("shell", SHELLS)
def test_no_env_file_is_written_without_randomness(shell: str, sandbox: dict[str, Path]) -> None:
    _executable(sandbox["bin"] / "openssl", BROKEN_OPENSSL)
    shutil.copy(INSTALL_DIR / "install.sh", sandbox["site"] / "install.sh")

    done = _run(shell, sandbox, "--url", "http://localhost:8080", "--yes")

    assert done.returncode != 0, done.stdout
    assert not (sandbox["site"] / ".env").exists(), "빈 비밀로 .env 를 썼다: " + (
        sandbox["site"] / ".env"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("shell", SHELLS)
def test_a_fresh_install_writes_full_secrets(shell: str, sandbox: dict[str, Path]) -> None:
    """위 시험이 "아무것도 안 쓰는 스크립트" 로 통과하는 것을 막는다."""
    shutil.copy(INSTALL_DIR / "install.sh", sandbox["site"] / "install.sh")
    done = _run(shell, sandbox, "--url", "http://localhost:8080", "--yes")
    assert done.returncode == 0, done.stderr

    env = _env(sandbox["site"])
    assert len(env["IEUM_SECRET_KEY"]) >= 32
    assert len(env["IEUM_DB_PASSWORD"]) >= 32
    assert len(env["IEUM_S3_SECRET_KEY"]) >= 32
    assert len(env["IEUM_S3_ACCESS_KEY"]) == 20
    assert len(env["SEED_ADMIN_PASSWORD"]) == 20
    assert stat.S_IMODE((sandbox["site"] / ".env").stat().st_mode) == 0o600


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("url", "hops"), [("http://localhost:8080", "1"), ("https://ieum.example.com", "2")]
)
def test_the_proxy_hops_follow_the_address(
    shell: str, sandbox: dict[str, Path], url: str, hops: str
) -> None:
    """https 면 TLS 를 끝내는 프록시가 nginx 앞에 하나 더 있다.

    1 로 남으면 모든 접속자가 그 프록시 주소 하나로 보여, IP 기준 로그인
    제한이 몇 번의 실패로 전원을 막는다.
    """
    shutil.copy(INSTALL_DIR / "install.sh", sandbox["site"] / "install.sh")
    done = _run(shell, sandbox, "--url", url, "--yes")
    assert done.returncode == 0, done.stderr
    assert _env(sandbox["site"])["IEUM_TRUSTED_PROXY_HOPS"] == hops
