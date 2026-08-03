#!/usr/bin/env python3
"""`agents/researcher.agent.yaml` の `input_schema` を schemas/article.schema.json に揃える。

custom tool `submit_article` の `input_schema` は article schema **そのもの**
(docs/design.md §5.1)。ところが `ant beta:agents create < agents/researcher.agent.yaml`
が単体で通ることが issue #4 の完了条件なので、YAML 側に実体を持たせるしかない。

そこで「写しを置く。ただし機械で同期し、テストで見張る」という形にした。

    make agents-sync    # 写しを更新する
    make test           # ずれていたら tests/test_agents.py が落ちる

`input_schema:` の行から下は全部このスクリプトが書く。手で足したものは消える。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_YAML = REPO_ROOT / "agents" / "researcher.agent.yaml"
ARTICLE_SCHEMA = REPO_ROOT / "schemas" / "article.schema.json"

MARKER = "    input_schema:"
"""この行以降を丸ごと差し替える。ファイル末尾に 1 回だけ現れる想定。"""

INDENT = " " * 6
"""JSON ブロックのインデント。`input_schema:` より深ければ YAML のフロー記法になる。"""


def render(schema: dict) -> str:
    """`input_schema:` 行と、その値になる JSON ブロックを返す。"""
    body = json.dumps(schema, ensure_ascii=False, indent=2)
    indented = "\n".join(INDENT + line for line in body.splitlines())
    return f"{MARKER}\n{indented}\n"


def build() -> str:
    """同期後の YAML 全文を組み立てる。"""
    text = AGENT_YAML.read_text(encoding="utf-8")
    head, sep, _ = text.partition(MARKER)
    if not sep:
        sys.exit(f"{AGENT_YAML} に `{MARKER.strip()}` の行がない。目印を消さないこと")
    schema = json.loads(ARTICLE_SCHEMA.read_text(encoding="utf-8"))
    return head + render(schema)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="書き換えずに差分の有無だけ見る")
    args = parser.parse_args()

    want = build()
    if AGENT_YAML.read_text(encoding="utf-8") == want:
        print(f"{AGENT_YAML.relative_to(REPO_ROOT)}: 同期済み")
        return 0
    if args.check:
        print(
            f"{AGENT_YAML.relative_to(REPO_ROOT)} が "
            f"{ARTICLE_SCHEMA.relative_to(REPO_ROOT)} とずれている。"
            "`make agents-sync` を実行すること",
            file=sys.stderr,
        )
        return 1
    AGENT_YAML.write_text(want, encoding="utf-8")
    print(f"{AGENT_YAML.relative_to(REPO_ROOT)}: 更新した")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
