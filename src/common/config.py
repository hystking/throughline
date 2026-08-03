"""`config/pipeline.yaml` を読み、環境変数で上書きし、`${VAR}` を解決する。

規則は `docs/design.md` §7.6。適用順が重要:

1. YAML を読む
2. ``AINEWS__<SECTION>__<KEY>`` 形式の環境変数で上書きする (ネストは ``__`` 区切り)
3. 残った ``${VAR}`` を環境変数から解決する。**1 つでも未定義なら起動時に落とす**

上書きを先に適用するので、``${DATA_BUCKET}`` を解決させる代わりに
``AINEWS__STORAGE__DATA_BUCKET`` で直接値を渡してもよい。
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "AINEWS__"
"""上書き用の環境変数の接頭辞。"""

CONFIG_PATH_ENV = "AINEWS_CONFIG_PATH"
"""pipeline.yaml の場所を明示したいときに使う環境変数。"""

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_MISSING = object()


class ConfigError(Exception):
    """設定の読み込み・検証に失敗した。起動時に落とすための例外。"""


@dataclass(frozen=True)
class Config:
    """解決済みの設定。ドット区切りのパスで引く。"""

    data: dict[str, Any] = field(repr=False)
    path: Path

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """``config.get("models.research.effort")`` の形で引く。

        キーが無ければ ``default``。``default`` も無ければ :class:`ConfigError`。
        """
        node: Any = self.data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                if default is _MISSING:
                    raise ConfigError(f"設定キーが無い: {dotted} ({self.path})")
                return default
            node = node[key]
        return node

    def __getitem__(self, dotted: str) -> Any:
        return self.get(dotted)

    def section(self, name: str) -> dict[str, Any]:
        """トップレベルのセクションを dict で返す。"""
        value = self.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"{name} はセクションではない: {type(value).__name__}")
        return value

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.data)


def load_config(
    path: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """pipeline.yaml を読み、上書きと `${VAR}` 解決を済ませた :class:`Config` を返す。"""
    env = dict(os.environ if environ is None else environ)
    config_path = Path(path) if path is not None else _default_config_path(env)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"設定ファイルが無い: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML として読めない: {config_path}\n{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"設定のトップレベルはマッピングであること: {config_path}")

    data = _apply_env_overrides(raw, env)
    data = _resolve_placeholders(data, env, config_path)
    return Config(data=data, path=config_path)


_cached: Config | None = None


def get_config() -> Config:
    """プロセス内でキャッシュした設定を返す (Lambda のウォームスタート用)。"""
    global _cached
    if _cached is None:
        _cached = load_config()
    return _cached


def reset_config_cache() -> None:
    """キャッシュを捨てる。テストと `make run-local` の再実行用。"""
    global _cached
    _cached = None


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _default_config_path(env: dict[str, str]) -> Path:
    """`config/pipeline.yaml` を探す。

    リポジトリ (`src/common/config.py` から見て 2 つ上) と
    Lambda イメージ (`/var/task/common/config.py` から見て 1 つ上) の
    どちらのレイアウトでも当たるように、親を順にたどる。
    """
    if override := env.get(CONFIG_PATH_ENV):
        return Path(override)

    here = Path(__file__).resolve()
    for base in here.parents:
        candidate = base / "config" / "pipeline.yaml"
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"config/pipeline.yaml が見つからない。{CONFIG_PATH_ENV} で場所を指定すること"
    )


def _apply_env_overrides(raw: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    data = deepcopy(raw)
    for name in sorted(env):
        if not name.startswith(ENV_PREFIX):
            continue
        keys = [segment.lower() for segment in name[len(ENV_PREFIX) :].split("__")]
        if not keys or any(not key for key in keys):
            raise ConfigError(f"{name}: 上書き先のキーが空")
        _set_path(data, keys, _coerce(env[name]), name)
    return data


def _set_path(root: dict[str, Any], keys: list[str], value: Any, env_name: str) -> None:
    """存在するキーだけ上書きする。typo を黙って新キーとして通さない。"""
    node: Any = root
    for depth, key in enumerate(keys):
        path = ".".join(keys[: depth + 1])
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"{env_name}: {path} は pipeline.yaml に無いキー")
        if depth == len(keys) - 1:
            node[key] = value
        else:
            node = node[key]


def _coerce(raw: str) -> Any:
    """環境変数の文字列を YAML 側の型に寄せる。

    JSON として読めればその値 (数値・真偽・null・配列・オブジェクト)、
    読めなければ文字列のまま。``"high"`` や ``"07:00"`` は後者になる。
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _resolve_placeholders(data: dict[str, Any], env: dict[str, str], path: Path) -> dict[str, Any]:
    missing: list[str] = []

    def substitute(value: str, trail: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = env.get(name)
            if not resolved:  # 未定義も空文字も「未設定」として扱う
                missing.append(f"{name}  ({trail})")
                return match.group(0)
            return resolved

        return _PLACEHOLDER.sub(replace, value)

    def walk(node: Any, trail: str) -> Any:
        if isinstance(node, dict):
            return {
                key: walk(item, f"{trail}.{key}" if trail else key) for key, item in node.items()
            }
        if isinstance(node, list):
            return [walk(item, f"{trail}[{i}]") for i, item in enumerate(node)]
        if isinstance(node, str):
            return substitute(node, trail)
        return node

    resolved = walk(data, "")
    if missing:
        listed = "\n".join(f"  - {entry}" for entry in dict.fromkeys(missing))
        raise ConfigError(f"環境変数が未設定のため {path} を解決できない:\n{listed}")
    return resolved
