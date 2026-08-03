"""セッションの実行と結果の受け取り (`src/agents/client.py`)。

本物の API は叩かない。見張っているのは**壊れ方が静かなところ**:

- stream-first — ストリームを開く前に送ると、最初のイベントを取りこぼす
- `submit_article` に応えないと、セッションが idle のまま固まる
- idle だけで抜けると、結果待ちの idle で早じまいする
- 切断後に張り直しただけだと、切断中のイベントが永久に欠ける
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.client import ResearchError, agent_ref, run_research, trace_url
from common.config import Config
from conftest import AGENT_VERSION, SESSION_ID, FakeClient, event, idle, submit


def run(client: FakeClient, config: Config, prompt: str = "<article/>") -> Any:
    return run_research(prompt, client=client, config=config, title="research: test")


# --- 正常系 ---------------------------------------------------------------


def test_returns_the_submitted_article(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    client = make_client(
        [
            [
                event("session.status_running", "e1"),
                submit("e2", article_payload),
                idle("e3", "end_turn"),
            ]
        ]
    )
    result = run(client, config)

    assert result.article == article_payload
    assert result.session_id == SESSION_ID
    assert result.agent_version == AGENT_VERSION
    assert (
        result.trace_url
        == f"https://platform.claude.com/workspaces/wrkspc_test/sessions/{SESSION_ID}"
    )


def test_stream_opens_before_the_prompt_is_sent(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """SSE は開いた後のイベントしか流さない。送信が先だと頭を落とす。"""
    order: list[str] = []
    client = make_client([[submit("e1", article_payload), idle("e2")]])

    original_stream = client.events.stream
    original_send = client.events.send

    def spy_stream(session_id: str) -> Any:
        order.append("stream")
        return original_stream(session_id)

    def spy_send(session_id: str, *, events: Any) -> None:
        order.append("send")
        return original_send(session_id, events=events)

    client.events.stream = spy_stream
    client.events.send = spy_send

    run(client, config)
    assert order[0] == "stream"
    assert order[1] == "send"


def test_prompt_is_sent_as_a_user_message(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    run(client, config, prompt="<article><title>x</title></article>")

    sent = client.events.sent_of("user.message")
    assert sent == [
        {
            "type": "user.message",
            "content": [{"type": "text", "text": "<article><title>x</title></article>"}],
        }
    ]


def test_submit_article_gets_a_tool_result(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """返事をしないとセッションが結果待ちのまま idle で止まる。"""
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    run(client, config)

    results = client.events.sent_of("user.custom_tool_result")
    assert len(results) == 1
    assert results[0]["custom_tool_use_id"] == "e1"


def test_null_narrative_and_insider_survive_untouched(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """裏が取れなかったときの null。埋めさせないための逃げ道なので潰さない。"""
    article_payload |= {"narrative_ja": None, "insider_ja": None, "try_it": None}
    client = make_client([[submit("e1", article_payload), idle("e2")]])

    result = run(client, config)
    assert result.article["narrative_ja"] is None
    assert result.article["insider_ja"] is None
    assert result.article["try_it"] is None


def test_usage_is_summed_across_model_requests(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """キャッシュ分まで数える。

    セッション内はほぼ全部がキャッシュ読みになる。実測で input_tokens は
    1 セッション 14 トークンだった (キャッシュ分は 30 万超)。
    input/output だけ積むとコストが桁で狂う。
    """
    client = make_client(
        [
            [
                event(
                    "span.model_request_end",
                    "e1",
                    model_usage={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 7,
                        "cache_read_input_tokens": 5000,
                    },
                ),
                event(
                    "span.model_request_end",
                    "e2",
                    model_usage={"input_tokens": 300, "output_tokens": 40},
                ),
                submit("e3", article_payload),
                idle("e4"),
            ]
        ]
    )
    assert run(client, config).usage == {
        "input_tokens": 400,
        "output_tokens": 60,
        "cache_creation_input_tokens": 7,
        "cache_read_input_tokens": 5000,
    }


# --- 終了判定 -------------------------------------------------------------


def test_idle_awaiting_a_tool_result_is_not_the_end(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """`requires_action` の idle はこちら待ち。ここで抜けると結果を取り逃す。"""
    client = make_client(
        [
            [
                idle("e1", "requires_action"),
                submit("e2", article_payload),
                idle("e3", "requires_action"),
                idle("e4", "end_turn"),
            ]
        ]
    )
    assert run(client, config).article == article_payload


def test_terminated_ends_the_loop(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    client = make_client(
        [
            [
                submit("e1", article_payload),
                event("session.status_terminated", "e2"),
                submit("e3", {"title_ja": "この先は読まないはず"}),
            ]
        ]
    )
    assert run(client, config).article == article_payload


def test_session_error_does_not_abort_the_run(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """`session.error` はリトライ可能なこともある。終端イベントを待つ。"""
    client = make_client(
        [
            [
                event("session.error", "e1", error={"message": "transient"}),
                submit("e2", article_payload),
                idle("e3"),
            ]
        ]
    )
    assert run(client, config).article == article_payload


def test_no_submit_article_is_an_error(make_client: Any, config: Config) -> None:
    """例外にして Step Functions のリトライに乗せる (docs/design.md §9)。"""
    client = make_client([[event("agent.message", "e1"), idle("e2")]])
    with pytest.raises(ResearchError, match="submit_article"):
        run(client, config)


def test_the_first_submit_wins(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    second = article_payload | {"title_ja": "書き直し"}
    client = make_client([[submit("e1", article_payload), submit("e2", second), idle("e3")]])
    assert run(client, config).article["title_ja"] == article_payload["title_ja"]


# --- 切断からの復帰 -------------------------------------------------------


def test_reconnect_replays_the_history_it_missed(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """切断中に出た `agent.custom_tool_use` を履歴から拾えること。

    ここを落とすと `user.custom_tool_result` を返せず、セッションは
    結果待ちの idle のまま Lambda の timeout まで戻ってこない。
    """
    dropped = submit("e2", article_payload)
    client = make_client(
        [
            [event("session.status_running", "e1"), ConnectionError("切れた")],
            [],  # 張り直した先には新しいイベントは無い。履歴だけが頼り
        ],
        history=[event("session.status_running", "e1"), dropped, idle("e3", "end_turn")],
    )

    result = run(client, config)
    assert result.article == article_payload
    assert client.events.list_calls == 1
    assert client.events.streams_opened == 2


def test_replayed_history_is_deduplicated_by_event_id(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """live で見たイベントが履歴にも出る。二度処理すると tool_result も二重になる。"""
    seen_live = submit("e1", article_payload)
    client = make_client(
        [
            [seen_live, ConnectionError("切れた")],
            [],
        ],
        history=[seen_live, idle("e2", "end_turn")],
    )
    run(client, config)
    assert len(client.events.sent_of("user.custom_tool_result")) == 1


def test_the_prompt_is_not_sent_again_on_reconnect(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    client = make_client(
        [[ConnectionError("切れた")], []],
        history=[submit("e1", article_payload), idle("e2", "end_turn")],
    )
    run(client, config)
    assert len(client.events.sent_of("user.message")) == 1


def test_giving_up_after_too_many_reconnects(make_client: Any, config: Config) -> None:
    client = make_client([[ConnectionError("切れた")] for _ in range(10)])
    with pytest.raises(ResearchError, match="張り直しても"):
        run(client, config)


def test_a_stream_that_closes_without_a_terminal_event_is_retried(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    """例外を出さずに閉じることもある。黙って成功扱いにしない。"""
    client = make_client(
        [[], []],
        history=[submit("e1", article_payload), idle("e2", "end_turn")],
    )
    assert run(client, config).article == article_payload


def test_an_endlessly_empty_stream_gives_up(make_client: Any, config: Config) -> None:
    client = make_client([[] for _ in range(10)])
    with pytest.raises(ResearchError, match="終端イベント無し"):
        run(client, config)


# --- 設定まわり -----------------------------------------------------------


def test_agent_version_is_pinned_when_configured(config: Config) -> None:
    """バージョンを固定するのは出力品質の変化を追うため (docs/design.md §4-③)。"""
    assert agent_ref(config) == {"type": "agent", "id": "agent_test", "version": AGENT_VERSION}


def test_agent_id_alone_means_latest(config: Config) -> None:
    config.data["managed_agents"]["pin_agent_version"] = False
    assert agent_ref(config) == "agent_test"


def test_pinning_without_a_version_fails_fast(config: Config) -> None:
    config.data["managed_agents"]["agent_version"] = ""
    with pytest.raises(ResearchError, match="agent_version"):
        agent_ref(config)


def test_session_is_created_from_the_configured_ids(
    make_client: Any, config: Config, article_payload: dict[str, Any]
) -> None:
    client = make_client([[submit("e1", article_payload), idle("e2")]])
    run(client, config)

    created = client.sessions.created[0]
    assert created["environment_id"] == "env_test"
    assert created["agent"] == {"type": "agent", "id": "agent_test", "version": AGENT_VERSION}
    # 作成時にイベントを渡さない。渡すと開く前にエージェントが動き出す
    assert "initial_events" not in created


def test_trace_url_is_omitted_without_a_workspace(config: Config) -> None:
    config.data["managed_agents"]["workspace_id"] = ""
    assert trace_url(config, SESSION_ID) is None
