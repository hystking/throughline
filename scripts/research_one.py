#!/usr/bin/env python3
"""記事 1 本を本物の Managed Agents で調査させ、`article.json` を得る。

issue #5 の完了条件「手で用意した記事 1 本から、検証を通った `article.json` が
得られる」を人の手で確かめるための入り口。Triage (#6〜#8) がまだ無いあいだ、
Research だけを単体で回す。**本番と同じ API を叩く** (課金される)。

    make research-sample > /tmp/one.json     # 雛形を出して手で書き換える
    make research-one ARTICLE=/tmp/one.json

Agent / Environment の ID は `make agents-apply` が `.env.agents` に落とす。
それに加えて `ANTHROPIC_API_KEY` と、Console のトレース URL に使う
`ANTHROPIC_WORKSPACE_ID` が要る (未設定なら Makefile が `default` を入れる)。

書き出し先は `./.local/data/runs/<date>/articles/<id>.json`。S3 には触らない。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common.config import get_config  # noqa: E402
from common.logging import configure_logging  # noqa: E402
from handlers.research import research_article  # noqa: E402

SAMPLE = {
    "id": "0123456789ab",
    "title": "Anthropic releases Claude Opus 5",
    "url": "https://www.anthropic.com/news/claude-opus-5",
    "published_at": "2026-07-31T17:02:00Z",
    "category": "ai",
    "sources": [
        {
            "kind": "hackernews",
            "score": 842,
            "num_comments": 391,
            "discussion_url": "https://news.ycombinator.com/item?id=44821001",
        }
    ],
    "related_urls": [],
    "excerpt": "",
}
"""`--sample` で出す雛形。`id` は 12 桁の 16 進 (Triage が SHA1 から作る形)。"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article", nargs="?", help="記事 1 本を書いた JSON ファイル")
    parser.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--sample", action="store_true", help="入力の雛形を出して終わる")
    args = parser.parse_args()

    if args.sample:
        print(json.dumps(SAMPLE, ensure_ascii=False, indent=2))
        return 0
    if not args.article:
        parser.error("記事の JSON ファイルを渡すこと (雛形は --sample)")

    article = json.loads(Path(args.article).read_text(encoding="utf-8"))

    config = get_config()
    configure_logging(config)
    stored = research_article(args.date, article, config=config)

    print(json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
