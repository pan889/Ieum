"""compose 파일을 읽는 한 벌.

**`yaml.safe_load` 로는 못 읽는다.** 운영 오버라이드가 compose 스펙의 병합
태그(`!reset`·`!override`)를 쓰는데, safe_load 는 모르는 태그를 만나면 던진다.
읽는 자리가 여럿이라(설치 게이트·설정 게이트) 각자 처리하면 다음에 태그를
하나 더 쓰는 날 한쪽만 죽는다.

태그는 **떼고 알맹이만** 읽는다. 우리가 보는 것은 "무엇이 적혀 있나" 이고,
실제 병합 결과가 궁금한 자리는 `docker compose config` 를 부른다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ComposeLoader(yaml.SafeLoader):
    """`!reset` 같은 지역 태그를 값으로 읽는 로더."""


def _tagged_value(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> Any:
    """태그를 떼고 알맹이만 읽는다.

    같은 노드를 `construct_object` 로 다시 던지면 자기 자신을 기다리다
    "unconstructable recursive node" 로 죽는다 — 한 번 그렇게 했다.
    """
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _tagged_value)


def read_compose(path: Path) -> dict[str, Any]:
    """`${VAR:?...}` 가 들어 있어도 YAML 로는 그냥 문자열이라 그대로 읽힌다."""
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)  # noqa: S506
    assert isinstance(loaded, dict)
    return loaded


__all__ = ["ComposeLoader", "read_compose"]
