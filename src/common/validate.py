"""`schemas/*.json` による検証。パイプライン全体の契約はここを通る。

3 つの schema がある (`docs/design.md` §5):

- ``article`` — Research の成果物。**custom tool `submit_article` の `input_schema`
  としてそのまま使う** ので、エージェントが書くフィールドしか入っていない
- ``digest``  — Synthesize の成果物
- ``index``   — ``state/index.json``

失敗したら :class:`ValidationFailed` を投げる。メッセージには
**どのフィールドが何で落ちたか**を全件並べる。1 個直すたびに再実行する羽目に
ならないようにするため。

    >>> validate_article(payload)          # 失敗したら例外
    >>> errors = check("article", payload) # 例外にせず一覧が欲しいとき

Research で得た記事に Lambda が ``id`` などを足したものは
:func:`validate_stored_article` で見る。付与フィールドの定義は
``digest.schema.json`` の ``$defs.lambda_fields`` にあり、article schema 側とは
重複していない。
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_NAMES = ("article", "digest", "index")

SCHEMA_DIR_ENV = "AINEWS_SCHEMA_DIR"
"""schemas/ の場所を明示したいときに使う環境変数。"""

LAMBDA_ADDED_FIELDS = (
    "id",
    "collected_at",
    "session_id",
    "agent_version",
    "source_mentions",
    "usage",
)
"""Research の後で Lambda が付け足すフィールド (`docs/design.md` §5.1 末尾)。

エージェントには書かせないので ``article.schema.json`` には無い。
``additionalProperties: false`` の schema にそのまま渡すと落ちるため、
:func:`validate_stored_article` はこれを剥がしてから article schema にかける。
"""


class ValidationFailed(Exception):
    """schema 検証に失敗した。``errors`` に人が読める行が入る。"""

    def __init__(self, schema_name: str, errors: list[str], *, subject: str | None = None) -> None:
        self.schema_name = schema_name
        self.errors = errors
        where = f" ({subject})" if subject else ""
        listed = "\n".join(f"  - {line}" for line in errors)
        super().__init__(f"{schema_name}.schema.json の検証に失敗{where}:\n{listed}")


# ---------------------------------------------------------------------------
# schema の読み込み
# ---------------------------------------------------------------------------


def schema_dir() -> Path:
    """`schemas/` を探す。

    リポジトリ (`src/common/validate.py` から見て 2 つ上) と Lambda イメージ
    (`/var/task/common/validate.py` から見て 1 つ上) のどちらでも当たるように、
    `common/config.py` と同じく親を順にたどる。
    """
    if override := os.environ.get(SCHEMA_DIR_ENV):
        return Path(override)

    for base in Path(__file__).resolve().parents:
        candidate = base / "schemas"
        if (candidate / "article.schema.json").is_file():
            return candidate
    raise FileNotFoundError(f"schemas/ が見つからない。{SCHEMA_DIR_ENV} で場所を指定すること")


@cache
def load_schema(name: str) -> dict[str, Any]:
    """``name`` の schema を dict で返す。プロセス内でキャッシュする。"""
    if name not in SCHEMA_NAMES:
        raise KeyError(f"未知の schema: {name} (あるのは {', '.join(SCHEMA_NAMES)})")
    return json.loads((schema_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))


def article_tool_input_schema() -> dict[str, Any]:
    """custom tool ``submit_article`` の ``input_schema``。

    ``article.schema.json`` を**加工せずそのまま**返す (`docs/design.md` §5.1)。
    二重管理を避けるため、Agent 定義 (`agents/researcher.agent.yaml`) もこれを使う。
    呼び出し側が書き換えてもキャッシュを壊さないよう複製を返す。
    """
    return deepcopy(load_schema("article"))


@cache
def _registry() -> Registry:
    """schema 同士の ``$ref`` を解決できるようにまとめて登録する。"""
    resources = []
    for name in SCHEMA_NAMES:
        schema = load_schema(name)
        resources.append((schema["$id"], Resource.from_contents(schema)))
        # `$ref: "digest.schema.json#/$defs/date"` のような相対参照も引けるようにする
        resources.append((f"{name}.schema.json", Resource.from_contents(schema)))
    return Registry().with_resources(resources)


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name), registry=_registry())


@cache
def _ref_validator(name: str, pointer: str) -> Draft202012Validator:
    """schema の一部だけを取り出して使う。

    dict を切り出して渡すと基準 URI ごと失われて内側の ``#/$defs/…`` が解決できなくなる。
    ``$ref`` 1 個の schema にして、基準 URI をその schema の ``$id`` に固定する。
    """
    return Draft202012Validator(
        {"$ref": f"{load_schema(name)['$id']}#{pointer}"}, registry=_registry()
    )


def reset_schema_cache() -> None:
    """キャッシュを捨てる。テストと schema を編集しながらの `make run-local` 用。"""
    load_schema.cache_clear()
    _registry.cache_clear()
    _validator.cache_clear()
    _ref_validator.cache_clear()


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------


def check(name: str, instance: Any) -> list[str]:
    """検証して、失敗した箇所を人が読める行のリストで返す。空なら合格。"""
    errors = sorted(_validator(name).iter_errors(instance), key=lambda err: list(err.absolute_path))
    return [_describe(err) for err in errors]


def validate(name: str, instance: Any, *, subject: str | None = None) -> None:
    """検証して、落ちたら :class:`ValidationFailed` を投げる。

    ``subject`` は例外メッセージに添える手がかり (記事 id やファイル名など)。
    """
    if errors := check(name, instance):
        raise ValidationFailed(name, errors, subject=subject)


def validate_article(instance: Any, *, subject: str | None = None) -> None:
    """Research の生の成果物 (エージェントが書いたまま) を検証する。"""
    validate("article", instance, subject=subject)


def validate_digest(instance: Any, *, subject: str | None = None) -> None:
    validate("digest", instance, subject=subject)


def validate_index(instance: Any, *, subject: str | None = None) -> None:
    validate("index", instance, subject=subject)


def validate_stored_article(instance: Any, *, subject: str | None = None) -> None:
    """``runs/<date>/articles/<id>.json`` に置く形 (Lambda 付与フィールド込み) を検証する。

    article schema は ``additionalProperties: false`` なので、付与フィールドを
    剥がした本体を article schema に、付与フィールドを
    ``digest.schema.json#/$defs/lambda_fields`` にかける。
    """
    if not isinstance(instance, dict):
        raise ValidationFailed(
            "article",
            [f"(root): オブジェクトであること (今 {type(instance).__name__})"],
            subject=subject,
        )

    body = {key: value for key, value in instance.items() if key not in LAMBDA_ADDED_FIELDS}
    errors = check("article", body)

    added = _ref_validator("digest", "/$defs/lambda_fields")
    errors += [
        _describe(err)
        for err in sorted(added.iter_errors(instance), key=lambda err: list(err.absolute_path))
    ]

    if errors:
        raise ValidationFailed("article", errors, subject=subject)


def _describe(error: Any) -> str:
    """jsonschema のエラーを ``articles[0].tags: …`` の形に落とす。"""
    return f"{_pointer(error.absolute_path)}: {error.message}"


def _pointer(path: Any) -> str:
    parts = list(path)
    if not parts:
        return "(root)"
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else str(part)
    return rendered
