"""Helm 차트를 **렌더해서** 본다.

`helm lint` 와 `kubectl apply --dry-run=server` 는 매니페스트 하나하나가 맞는지를
본다. 그런데 이 차트의 큰 고장 둘은 **매니페스트 사이의 관계**에 있었고, 둘 다
그 검증을 통과했다:

- **마이그레이션 훅이 아직 없는 것을 가리켰다.** `pre-install` 훅은 일반
  리소스보다 먼저 만들어지는데, Job 이 차트의 `-config`·`-secrets`·서비스
  어카운트를 읽었다. 첫 `helm install` 은 Job 파드를 못 띄우고 시간 초과로
  실패했고, 일반 리소스는 그 뒤에 만들어지므로 끝내 안 생겼다.
- **웹 파드가 API 를 `api:8000` 으로 찾았다.** compose 의 서비스 이름이다.
  차트의 Service 는 `<fullname>-api` 라, nginx 가 기동하자마자 "host not found
  in upstream" 으로 죽었다.

그래서 여기서는 렌더 결과를 놓고 **이름이 서로 맞는지**를 본다.

`helm` 이 없으면 건너뛴다 — 로컬 필수 도구가 아니다. **CI 에서는 건너뛰지
않는다**(`CI` 환경변수). 조용히 건너뛰면 "통과했다" 가 거짓이 된다(Makefile 의
shellcheck 와 같은 사정).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CHART = REPO / "deploy" / "helm" / "ieum"

REQUIRED: dict[str, Any] = {
    "config": {
        "baseUrl": "https://ieum.example.com",
        "webOrigins": "https://ieum.example.com",
        "s3PublicEndpointUrl": "https://files.example.com",
    },
}
VALUE_SECRETS: dict[str, Any] = {
    "secrets": {
        "secretKey": "x" * 32,
        "databaseUrl": "postgresql+asyncpg://u:p@db:5432/ieum",
        "redisUrl": "redis://redis:6379/0",
    }
}
EXISTING_SECRET: dict[str, Any] = {"secrets": {"existingSecret": "ieum-secrets"}}


def _merge(*layers: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = _merge(out[key], value)
            else:
                out[key] = value
    return out


def _helm() -> str:
    found = shutil.which("helm")
    if found is None:
        if os.environ.get("CI"):
            pytest.fail("CI 에 helm 이 없다 — 차트 시험이 아무것도 안 본다 (ci.yml 의 setup-helm)")
        pytest.skip("helm 이 없다")
    return found


class _UniqueKeyLoader(yaml.SafeLoader):
    """같은 키가 두 번이면 던진다. PyYAML 은 기본으로 뒤의 것을 조용히 쓴다."""


def _construct_unique(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        assert key not in seen, f"같은 키가 두 번 찍혔다: {key}"
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique,
)


def _run(values: dict[str, Any], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
    return subprocess.run(  # noqa: S603 - 인자가 전부 우리가 적은 것이다
        [_helm(), "template", "r", str(CHART), "-f", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _render(values: dict[str, Any], tmp_path: Path) -> list[dict[str, Any]]:
    done = _run(values, tmp_path)
    assert done.returncode == 0, done.stderr
    docs = yaml.load_all(done.stdout, Loader=_UniqueKeyLoader)
    return [doc for doc in docs if doc]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    names = [(d["kind"], d["metadata"]["name"]) for d in docs]
    raise AssertionError(f"{kind}/{name} 가 없다: {names}")


def _hook(doc: dict[str, Any]) -> tuple[set[str], int] | None:
    annotations = doc["metadata"].get("annotations") or {}
    if "helm.sh/hook" not in annotations:
        return None
    phases = {phase.strip() for phase in annotations["helm.sh/hook"].split(",")}
    return phases, int(annotations.get("helm.sh/hook-weight", "0"))


def _containers(doc: dict[str, Any]) -> list[dict[str, Any]]:
    spec = doc["spec"]["template"]["spec"]
    return list(spec.get("containers", [])) + list(spec.get("initContainers", []))


def _references(doc: dict[str, Any]) -> set[tuple[str, str]]:
    """파드가 기동하려면 있어야 하는 것들. `optional` 참조도 센다 — Secret
    자체가 없으면 optional 키라도 파드는 그 Secret 을 찾는다."""
    found: set[tuple[str, str]] = set()
    spec = doc["spec"]["template"]["spec"]
    found.add(("ServiceAccount", spec.get("serviceAccountName", "default")))
    for container in _containers(doc):
        for source in container.get("envFrom", []):
            if "configMapRef" in source:
                found.add(("ConfigMap", source["configMapRef"]["name"]))
            if "secretRef" in source:
                found.add(("Secret", source["secretRef"]["name"]))
        for env in container.get("env", []):
            ref = env.get("valueFrom", {})
            if "secretKeyRef" in ref:
                found.add(("Secret", ref["secretKeyRef"]["name"]))
            if "configMapKeyRef" in ref:
                found.add(("ConfigMap", ref["configMapKeyRef"]["name"]))
    return found


VARIANTS = {
    "values-secrets": _merge(REQUIRED, VALUE_SECRETS),
    "existing-secret": _merge(REQUIRED, EXISTING_SECRET),
    "own-service-account": _merge(
        REQUIRED, VALUE_SECRETS, {"serviceAccount": {"create": False, "name": "ieum-runner"}}
    ),
}

#: 차트 밖에서 이미 있는 것들. 훅이 가리켜도 된다.
EXTERNAL = {("Secret", "ieum-secrets"), ("ServiceAccount", "ieum-runner")}


class TestTheMigrateHookCanStartOnAFreshInstall:
    @pytest.mark.parametrize("variant", sorted(VARIANTS))
    def test_everything_it_needs_is_made_before_it(self, variant: str, tmp_path: Path) -> None:
        docs = _render(VARIANTS[variant], tmp_path)
        job = _find(docs, "Job", "r-ieum-migrate-1")
        hook = _hook(job)
        assert hook is not None and "pre-install" in hook[0], "pre-install 훅이 아니다"
        weight = hook[1]

        earlier = set()
        for doc in docs:
            found = _hook(doc)
            if found and "pre-install" in found[0] and found[1] < weight:
                earlier.add((doc["kind"], doc["metadata"]["name"]))

        missing = _references(job) - earlier - EXTERNAL
        assert not missing, (
            f"pre-install 시점에 없는 것을 가리킨다: {sorted(missing)} — 일반 리소스는 훅이 "
            "끝난 뒤에 만들어진다"
        )

    def test_the_copies_say_the_same_as_the_real_ones(self, tmp_path: Path) -> None:
        """사본이 갈라지면 마이그레이션만 다른 DB·다른 키로 돈다."""
        docs = _render(VARIANTS["values-secrets"], tmp_path)
        assert (
            _find(docs, "ConfigMap", "r-ieum-migrate-config")["data"]
            == _find(docs, "ConfigMap", "r-ieum-config")["data"]
        )
        assert (
            _find(docs, "Secret", "r-ieum-migrate-secrets")["stringData"]
            == _find(docs, "Secret", "r-ieum-secrets")["stringData"]
        )

    def test_the_copies_do_not_outlive_a_successful_run(self, tmp_path: Path) -> None:
        """비밀의 사본이 클러스터에 남지 않게 한다."""
        docs = _render(VARIANTS["values-secrets"], tmp_path)
        secret = _find(docs, "Secret", "r-ieum-migrate-secrets")
        policy = secret["metadata"]["annotations"]["helm.sh/hook-delete-policy"]
        assert "hook-succeeded" in policy
        assert "before-hook-creation" in policy

    def test_the_app_still_reads_the_release_resources(self, tmp_path: Path) -> None:
        """사본은 훅만 쓴다. 앱이 사본을 가리키면 사본을 지우는 순간 앱이 죽는다."""
        docs = _render(VARIANTS["values-secrets"], tmp_path)
        for name in ("r-ieum-api", "r-ieum-worker"):
            refs = _references(_find(docs, "Deployment", name))
            assert ("ConfigMap", "r-ieum-config") in refs
            assert ("Secret", "r-ieum-secrets") in refs
            assert not {ref for ref in refs if "migrate" in ref[1]}, refs


class TestTheWebPodFindsTheApi:
    def test_the_upstream_is_the_api_service(self, tmp_path: Path) -> None:
        docs = _render(VARIANTS["values-secrets"], tmp_path)
        service = _find(docs, "Service", "r-ieum-api")
        port = service["spec"]["ports"][0]["port"]
        web = _find(docs, "Deployment", "r-ieum-web")
        env = {e["name"]: e.get("value") for e in _containers(web)[0].get("env", [])}
        assert env.get("IEUM_API_UPSTREAM") == f"r-ieum-api:{port}", env


class TestRequiredValues:
    def test_the_base_url_is_required(self, tmp_path: Path) -> None:
        values = _merge(VARIANTS["values-secrets"], {"config": {"baseUrl": ""}})
        done = _run(values, tmp_path)
        assert done.returncode != 0
        assert "config.baseUrl" in done.stderr

    def test_the_app_is_told_its_address_and_storage(self, tmp_path: Path) -> None:
        values = _merge(
            VARIANTS["values-secrets"],
            {
                "config": {"s3EndpointUrl": "http://minio.storage.svc:9000"},
                "secrets": {"s3AccessKey": "access", "s3SecretKey": "secret"},
            },
        )
        docs = _render(values, tmp_path)
        data = _find(docs, "ConfigMap", "r-ieum-config")["data"]
        assert data["IEUM_BASE_URL"] == "https://ieum.example.com"
        assert data["IEUM_S3_ENDPOINT_URL"] == "http://minio.storage.svc:9000"
        assert data["IEUM_S3_BUCKET"] == "ieum-attachments"
        secret = _find(docs, "Secret", "r-ieum-secrets")["stringData"]
        assert secret["IEUM_S3_ACCESS_KEY"] == "access"
        api = _containers(_find(docs, "Deployment", "r-ieum-api"))[0]
        names = {e["name"] for e in api["env"]}
        assert {"IEUM_S3_ACCESS_KEY", "IEUM_S3_SECRET_KEY"} <= names

    def test_storage_keys_are_optional_for_an_existing_secret(self, tmp_path: Path) -> None:
        """IAM 역할로 붙는 S3 는 열쇠가 없다. 열쇠가 없다고 파드가 안 뜨면 안 된다."""
        docs = _render(VARIANTS["existing-secret"], tmp_path)
        api = _containers(_find(docs, "Deployment", "r-ieum-api"))[0]
        for env in api["env"]:
            if env["name"] in {"IEUM_S3_ACCESS_KEY", "IEUM_S3_SECRET_KEY"}:
                assert env["valueFrom"]["secretKeyRef"].get("optional") is True, env


class TestTheFirstAdmin:
    def test_the_seed_job_is_given_the_admin(self, tmp_path: Path) -> None:
        values = _merge(
            VARIANTS["values-secrets"],
            {
                "seed": {"adminEmail": "root@ieum.example.com"},
                "secrets": {"seedAdminPassword": "pw"},
            },
        )
        docs = _render(values, tmp_path)
        assert _find(docs, "Secret", "r-ieum-secrets")["stringData"]["SEED_ADMIN_PASSWORD"] == "pw"
        seed = _containers(_find(docs, "Job", "r-ieum-seed-1"))[0]
        env = {e["name"]: e for e in seed["env"]}
        assert env["SEED_ADMIN_EMAIL"]["value"] == "root@ieum.example.com"
        assert env["SEED_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "r-ieum-secrets",
            "key": "SEED_ADMIN_PASSWORD",
            "optional": True,
        }

    def test_only_the_seed_job_sees_the_password(self, tmp_path: Path) -> None:
        docs = _render(VARIANTS["values-secrets"], tmp_path)
        for name in ("r-ieum-api", "r-ieum-worker"):
            env = {e["name"] for e in _containers(_find(docs, "Deployment", name))[0]["env"]}
            assert "SEED_ADMIN_PASSWORD" not in env


class TestExtrasDoNotCollide:
    def test_a_key_given_twice_is_written_once(self, tmp_path: Path) -> None:
        """`extra` 에 차트가 정하는 키를 같이 적으면 예전에는 YAML 에 같은 키가
        두 번 찍혔다. 이제는 차트의 값이 이긴다. 렌더를 읽는 로더가 같은 키를
        만나면 던진다."""
        values = _merge(
            VARIANTS["values-secrets"],
            {
                "config": {"extra": {"IEUM_BASE_URL": "http://wrong", "IEUM_SMTP_HOST": "smtp"}},
                "secrets": {"extra": {"IEUM_S3_ACCESS_KEY": "a", "IEUM_SMTP_PASSWORD": "p"}},
            },
        )
        docs = _render(values, tmp_path)
        data = _find(docs, "ConfigMap", "r-ieum-config")["data"]
        assert data["IEUM_BASE_URL"] == "https://ieum.example.com"
        assert data["IEUM_SMTP_HOST"] == "smtp"
        for doc in docs:
            if doc["kind"] not in {"Deployment", "Job"}:
                continue
            for container in _containers(doc):
                names = [e["name"] for e in container.get("env", [])]
                assert len(names) == len(set(names)), (doc["metadata"]["name"], names)
                # 웹(nginx)은 앱 설정을 안 받는다. 나머지는 extra 비밀을 다 받아야 한다.
                if doc["metadata"]["name"] != "r-ieum-web":
                    assert "IEUM_SMTP_PASSWORD" in names, doc["metadata"]["name"]


class TestTheIngressOnlyOpensRealPaths:
    def test_paths_to_the_api_exist_in_the_app(self, tmp_path: Path) -> None:
        """`/vcs` 가 API 로 가고 있었다. 앱에는 그 경로가 없다 — 웹훅은
        `/api/v1/vcs/...` 다."""
        ingress_on = {"ingress": {"enabled": True, "host": "ieum.example.com"}}
        values = _merge(VARIANTS["values-secrets"], ingress_on)
        docs = _render(values, tmp_path)
        ingress = _find(docs, "Ingress", "r-ieum")
        paths = {
            p["path"]: p["backend"]["service"]["name"]
            for rule in ingress["spec"]["rules"]
            for p in rule["http"]["paths"]
        }
        assert paths == {"/api": "r-ieum-api", "/scim": "r-ieum-api", "/": "r-ieum-web"}
