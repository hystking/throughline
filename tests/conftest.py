"""Managed Agents のセッションを、API を叩かずに再現するための道具。

本物の SDK は `beta.sessions.create` / `beta.sessions.events.{stream,send,list}`
しか使っていない (`src/agents/client.py`)。その 4 つだけを持つ偽物を置く。

ストリームは「接続 1 回ぶんのイベント列」のリストで書く。列の末尾に例外を
混ぜると、その接続が途中で切れる。再接続時に `events.list()` が返す履歴は
別に渡すので、**切断中に出たイベントを履歴側だけに置く**といった状況も作れる。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from common.config import Config

SESSION_ID = "sesn_test0001"
AGENT_VERSION = 7


def event(type_: str, id_: str | None = None, **fields: Any) -> SimpleNamespace:
    """セッションイベント 1 個。id を省くと「id を持たないイベント」になる。"""
    return SimpleNamespace(type=type_, id=id_, **fields)


def idle(id_: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return event("session.status_idle", id_, stop_reason=SimpleNamespace(type=stop_reason))


def submit(id_: str, payload: dict[str, Any]) -> SimpleNamespace:
    return event("agent.custom_tool_use", id_, name="submit_article", input=payload)


class FakeStream:
    """`with … as stream:` して回すだけ。例外が混ざっていればそこで切れる。"""

    def __init__(self, script: Sequence[Any]) -> None:
        self._script = list(script)
        self.closed = False

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.closed = True
        return False

    def __iter__(self) -> Iterator[Any]:
        for item in self._script:
            if isinstance(item, BaseException):
                raise item
            yield item


class FakeEvents:
    def __init__(self, connections: Sequence[Sequence[Any]], history: Sequence[Any]) -> None:
        self._connections = deque(connections)
        self.history = list(history)
        self.sent: list[dict[str, Any]] = []
        self.streams_opened = 0
        self.list_calls = 0

    def stream(self, session_id: str) -> FakeStream:
        self.streams_opened += 1
        script = self._connections.popleft() if self._connections else []
        return FakeStream(script)

    def send(self, session_id: str, *, events: Sequence[dict[str, Any]]) -> None:
        self.sent.extend(events)

    def list(self, session_id: str) -> list[Any]:
        self.list_calls += 1
        return list(self.history)

    def sent_of(self, type_: str) -> list[dict[str, Any]]:
        return [item for item in self.sent if item.get("type") == type_]


class FakeSessions:
    def __init__(self, events: FakeEvents, *, agent_version: Any = AGENT_VERSION) -> None:
        self.events = events
        self.created: list[dict[str, Any]] = []
        self._agent_version = agent_version

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(
            id=SESSION_ID,
            agent=SimpleNamespace(id="agent_test", version=self._agent_version),
        )


class FakeClient:
    def __init__(self, sessions: FakeSessions) -> None:
        self.beta = SimpleNamespace(sessions=sessions)

    @property
    def sessions(self) -> FakeSessions:
        return self.beta.sessions

    @property
    def events(self) -> FakeEvents:
        return self.beta.sessions.events


@pytest.fixture
def make_client():
    """`make_client([[…イベント…], [切断後の 2 本目…]], history=[…])`。"""

    def build(
        connections: Sequence[Sequence[Any]] = (),
        *,
        history: Sequence[Any] = (),
        agent_version: Any = AGENT_VERSION,
    ) -> FakeClient:
        events = FakeEvents(connections, history)
        return FakeClient(FakeSessions(events, agent_version=agent_version))

    return build


@pytest.fixture
def config() -> Config:
    """`config/pipeline.yaml` のうち Research が読むところだけ。"""
    return Config(
        data={
            "managed_agents": {
                "researcher_agent_id": "agent_test",
                "environment_id": "env_test",
                "workspace_id": "wrkspc_test",
                "pin_agent_version": True,
                "agent_version": str(AGENT_VERSION),
            },
            "limits": {"research_timeout_seconds": 660},
            "observability": {"log_session_trace_url": True},
        },
        path=Path("tests/conftest.py"),
    )


@pytest.fixture
def article_payload() -> dict[str, Any]:
    """エージェントが `submit_article` に渡す、検証を通る最小構成。"""
    return {
        "title_ja": "Anthropic が Claude Opus 5 を公開",
        "title_original": "Anthropic releases Claude Opus 5",
        "url": "https://www.anthropic.com/news/claude-opus-5",
        "category": "ai",
        "tags": ["Anthropic", "LLM", "Claude"],
        "importance": 4,
        "summary_ja": "あ" * 220,
        "key_points": ["コンテキストは 1M", "価格は据え置き"],
        "narrative_ja": "7月下旬の値下げ競争に続く動き",
        "actionable_ja": "既存の Opus 4.8 を使っているなら model 名を差し替えるだけで移行できる。",
        "insider_ja": "ベンチマークの条件が前回と違う点に注意。",
        "confidence": "high",
    }


@pytest.fixture
def selected_article() -> dict[str, Any]:
    """Triage が確定させた記事 1 本 (Research の入力)。"""
    return {
        "id": "a1b2c3d4e5f6",
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
        "related_urls": ["https://techcrunch.com/2026/07/31/anthropic-opus-5/"],
        "continues": {
            "date": "2026-07-28",
            "title": "A社が推論単価を4割下げ",
            "relation": "同じ競争の続き",
        },
        "excerpt": "Anthropic today announced Claude Opus 5.",
        "source_mentions": [
            {"source": "hackernews", "url": "https://news.ycombinator.com/item?id=44821001"}
        ],
    }
