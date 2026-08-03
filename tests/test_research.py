"""Research ハンドラ (`src/handlers/research.py`)。

issue #5 の完了条件「手で用意した記事 1 本から、検証を通った `article.json` が
得られる」をここで見る。Lambda が付け足すフィールドまで含めて
`validate_stored_article` を通ることまで確かめる。
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from agents.client import ResearchError
from common.config import Config
from common.storage import LocalStorage
from common.validate import LAMBDA_ADDED_FIELDS, ValidationFailed, validate_stored_article
from conftest import AGENT_VERSION, SESSION_ID, FakeClient, event, idle, submit
from handlers.research import research_article

DATE = "2026-08-01"
ARTICLE_ID = "a1b2c3d4e5f6"
KEY = f"runs/{DATE}/articles/{ARTICLE_ID}.json"


@pytest.fixture
def store(tmp_path: Any) -> LocalStorage:
    return LocalStorage(tmp_path)


def research(
    client: FakeClient, config: Config, store: LocalStorage, article: dict[str, Any]
) -> dict[str, Any]:
    return research_article(DATE, article, config=config, client=client, storage=store)


def test_one_article_becomes_a_validated_json_file(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    stored = research(client, config, store, selected_article)

    validate_stored_article(stored)  # 書く前に落ちているはずだが、念のため書いた物も見る
    assert store.read_json(KEY) == stored


def test_lambda_fields_are_added(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    """エージェントには書かせないフィールド (`docs/design.md` §5.1 末尾)。"""
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    stored = research(client, config, store, selected_article)

    assert set(LAMBDA_ADDED_FIELDS) <= set(stored)
    assert stored["id"] == ARTICLE_ID
    assert stored["session_id"] == SESSION_ID
    assert stored["agent_version"] == AGENT_VERSION
    assert stored["source_mentions"] == selected_article["source_mentions"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stored["collected_at"])


def test_the_agents_own_fields_are_not_rewritten(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    """Lambda は付け足すだけ。要約や見出しに手を入れない。"""
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    stored = research(client, config, store, selected_article)

    assert {key: stored[key] for key in article_payload} == article_payload


def test_null_narrative_and_insider_pass_validation(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    """裏が取れないときは null。**それで検証を通ることが要件** (issue #5)。"""
    article_payload |= {"narrative_ja": None, "insider_ja": None, "try_it": None}
    client = make_client([[submit("e1", article_payload), idle("e2")]])

    stored = research(client, config, store, selected_article)
    assert stored["narrative_ja"] is None
    assert stored["insider_ja"] is None
    assert stored["try_it"] is None


def test_a_broken_article_never_reaches_storage(
    make_client: Any, config: Config, store: LocalStorage, selected_article: dict[str, Any]
) -> None:
    """検証失敗は例外。半端な JSON を置くと Synthesize が読んでしまう。"""
    client = make_client([[submit("e1", {"title_ja": "必須項目が足りない"}), idle("e2")]])

    with pytest.raises(ValidationFailed):
        research(client, config, store, selected_article)
    assert not store.exists(KEY)


def test_the_prompt_carries_the_article(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    research(client, config, store, selected_article)

    sent = client.events.sent_of("user.message")[0]["content"][0]["text"]
    assert "<url>https://www.anthropic.com/news/claude-opus-5</url>" in sent
    assert '<continues date="2026-07-28"' in sent


def test_session_title_helps_find_the_trace(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    research(client, config, store, selected_article)
    assert client.sessions.created[0]["title"].startswith("research: Anthropic releases")


def test_missing_article_id_fails_before_the_api_is_touched(
    make_client: Any, config: Config, store: LocalStorage, selected_article: dict[str, Any]
) -> None:
    """id は Triage が確定させる (`docs/design.md` §4-②)。ここで作り直さない。"""
    del selected_article["id"]
    client = make_client([[event("agent.message", "e1"), idle("e2")]])

    with pytest.raises(ResearchError, match="id"):
        research(client, config, store, selected_article)
    assert client.sessions.created == []


def test_unknown_agent_version_still_validates(
    make_client: Any,
    config: Config,
    store: LocalStorage,
    selected_article: dict[str, Any],
    article_payload: dict[str, Any],
) -> None:
    """バージョンが取れないのは困るが、記事 1 本を落とすほどではない。"""
    config.data["managed_agents"]["agent_version"] = ""
    config.data["managed_agents"]["pin_agent_version"] = False
    client = make_client([[submit("e1", article_payload), idle("e2")]], agent_version=None)

    stored = research(client, config, store, selected_article)
    assert stored["agent_version"] == "unknown"
