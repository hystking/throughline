"""Research ステージ — 記事 1 本を Managed Agent に調べさせて JSON にする。

`docs/design.md` §4-③。Step Functions の Map から**記事 1 本ごとに**呼ばれる。

    { "date": "2026-08-01", "article": { …Triage が確定させた 1 本… } }

出力は ``s3://data/runs/<date>/articles/<article_id>.json``。
中身は「エージェントが書いたフィールド」+「Lambda が付けるフィールド」
(`docs/design.md` §5.1 末尾) で、``validate_stored_article`` がその両方を見る。

**失敗は握り潰さず例外にする。** Map の `Retry` (2 回) → `Catch` に乗せて、
1 本の失敗で全体を止めない。半分以上落ちたときだけパイプラインを失敗させる
判断は Step Functions 側の仕事 (`docs/design.md` §9)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents.client import ResearchError, ResearchResult, run_research
from agents.prompt import render_prompt
from common.config import Config, get_config
from common.logging import configure_logging, get_logger
from common.storage import Storage, storage_for
from common.validate import validate_stored_article

ARTICLE_KEY = "runs/{date}/articles/{article_id}.json"

TITLE_CHARS = 60
"""セッションのタイトル。Console の一覧で見分けがつけば十分。"""


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda エントリポイント。"""
    config = get_config()
    configure_logging(config)

    date = event.get("date")
    article = event.get("article")
    if not date or not isinstance(article, dict):
        raise ResearchError("入力は {'date': …, 'article': {…}} であること")

    stored = research_article(date, article, config=config)
    return {
        "date": date,
        "id": stored["id"],
        "key": ARTICLE_KEY.format(date=date, article_id=stored["id"]),
        "session_id": stored["session_id"],
        "usage": stored.get("usage", {}),
    }


def research_article(
    date: str,
    article: dict[str, Any],
    *,
    config: Config | None = None,
    client: Any | None = None,
    storage: Storage | None = None,
) -> dict[str, Any]:
    """記事 1 本を調査し、検証済みの JSON を書いて返す。"""
    cfg = config if config is not None else get_config()
    store = storage if storage is not None else storage_for("data", cfg)

    article_id = str(article.get("id") or "")
    if not article_id:
        raise ResearchError("記事に id が無い。id は Triage が確定させる (docs/design.md §4-②)")

    log = get_logger(__name__, article_id=article_id, date=date)
    log.info("research 開始", extra={"url": article.get("url")})

    result = run_research(
        render_prompt(article),
        client=client,
        config=cfg,
        title=_session_title(article),
        log=log,
    )

    stored = _with_lambda_fields(result, article, log=log)
    validate_stored_article(stored, subject=article_id)

    key = ARTICLE_KEY.format(date=date, article_id=article_id)
    store.write_json(key, stored)
    log.info("article.json を書いた", extra={"uri": store.uri(key)})
    return stored


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _with_lambda_fields(
    result: ResearchResult, article: dict[str, Any], *, log: Any
) -> dict[str, Any]:
    """エージェントの出力に、エージェントには書かせないフィールドを足す。

    足すものは `common.validate.LAMBDA_ADDED_FIELDS` と同じ顔ぶれ。
    ``id`` は Triage が決めたものをそのまま使う (ここで作り直さない)。
    """
    agent_version = result.agent_version
    if agent_version is None:
        # 品質の変化を追う手がかりが消える。落とすほどではないので印だけ残す
        log.warning("Agent バージョンが分からない。unknown として記録する")
        agent_version = "unknown"

    stored = dict(result.article)
    stored["id"] = str(article["id"])
    stored["collected_at"] = _now()
    stored["session_id"] = result.session_id
    stored["agent_version"] = agent_version
    stored["usage"] = dict(result.usage)
    if mentions := article.get("source_mentions"):
        stored["source_mentions"] = mentions
    return stored


def _session_title(article: dict[str, Any]) -> str:
    title = str(article.get("title") or article.get("url") or "").strip()
    return f"research: {title[:TITLE_CHARS]}"


def _now() -> str:
    """`digest.schema.json#/$defs/timestamp` の形 (UTC・秒精度)。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
