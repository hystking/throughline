"""Managed Agents の Research セッションを 1 本走らせる (`docs/design.md` §4-③)。

**記事 1 本 = セッション 1 つ。** Lambda がやるのは「セッションを作る / イベントを
受ける / custom tool に応える」だけで、エージェントループとサンドボックスは
Anthropic 側にある。

Agent と Environment は `ant` CLI が `agents/*.yaml` から適用する
(コントロールプレーン)。**ここから `agents.create()` を呼ばない。**
孤児 Agent が溜まり、バージョン固定の意味も失われる。

## このモジュールが引き受けている面倒

ストリーム開始前の取りこぼし
    セッション作成時に ``initial_events`` を渡すとその場でエージェントが動き出し、
    ストリームを開くまでのイベントを取りこぼす。SSE は開いた後のものしか流さない。
    そこで**イベントを渡さずにセッションを作り、ストリームを開いてから**
    最初の ``user.message`` を送る (stream-first)。

ストリームの切断
    SSE にリプレイは無い。張り直しただけでは切断中のイベントが永久に欠ける。
    再接続のたびに ``events.list()`` で履歴を取り、event id で重複排除してから
    live に合流する。``agent.custom_tool_use`` を取りこぼすと、こちらの
    ``user.custom_tool_result`` が来ないままセッションが idle で固まる。

終了判定
    ``session.status_idle`` だけで抜けてはいけない。custom tool の結果待ちでも
    idle になる。``session.status_terminated``、または idle かつ
    ``stop_reason.type != "requires_action"`` を終端とする。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from common.config import Config, get_config
from common.logging import get_logger

SUBMIT_TOOL = "submit_article"
"""構造化出力の受け口。Managed Agents に `output_config.format` が無いための代替。"""

ACCEPT_MESSAGE = "Accepted. End your turn now."
"""`submit_article` への返事。これ以上調べさせない。"""

CONSOLE_TRACE = "https://platform.claude.com/workspaces/{workspace}/sessions/{session}"

MAX_RECONNECTS = 3
"""ストリームを張り直す上限。超えたら諦めて Step Functions のリトライに任せる。"""

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
"""積算するトークン数。単価が違うのでキャッシュ分は分けたまま持つ。"""


class ResearchError(Exception):
    """セッションが記事を返せなかった。

    **例外にするのは意図的。** Step Functions の Map が `Retry` → `Catch` で
    拾い、1 本の失敗が全体を止めないようにしてある (`docs/design.md` §9)。
    """


@dataclass(frozen=True)
class ResearchResult:
    """1 セッションの成果。schema 検証は呼び出し側 (`handlers/research.py`) が行う。"""

    article: dict[str, Any]
    """エージェントが `submit_article` に渡した中身。手を加えていない。"""

    session_id: str
    agent_version: str | int | None
    usage: dict[str, int] = field(default_factory=dict)
    trace_url: str | None = None


# ---------------------------------------------------------------------------
# 設定から組み立てるもの
# ---------------------------------------------------------------------------


def agent_ref(config: Config) -> str | dict[str, Any]:
    """`sessions.create(agent=...)` に渡す参照を作る。

    ``pin_agent_version: true`` ならバージョンを固定する (再現性)。
    false なら ID だけ渡す — API はそれを「最新バージョン」と解釈する。
    """
    agent_id = config.get("managed_agents.researcher_agent_id")
    if not agent_id:
        raise ResearchError("managed_agents.researcher_agent_id が空")
    if not config.get("managed_agents.pin_agent_version", True):
        return str(agent_id)

    version = config.get("managed_agents.agent_version", None)
    if version in (None, ""):
        raise ResearchError("pin_agent_version が true なのに managed_agents.agent_version が空")
    return {"type": "agent", "id": str(agent_id), "version": _as_version(version)}


def trace_url(config: Config, session_id: str) -> str | None:
    """Console のトレース URL。workspace_id が無ければ None。"""
    workspace = config.get("managed_agents.workspace_id", "")
    if not workspace:
        return None
    return CONSOLE_TRACE.format(workspace=workspace, session=session_id)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def run_research(
    prompt: str,
    *,
    client: Any | None = None,
    config: Config | None = None,
    title: str | None = None,
    log: Any | None = None,
) -> ResearchResult:
    """セッションを 1 つ起動し、`submit_article` の中身を受け取って返す。

    ``client`` を渡さなければ `anthropic.Anthropic()` を遅延生成する
    (認証は環境変数。`docs/design.md` §8)。
    """
    cfg = config if config is not None else get_config()
    api = client if client is not None else _default_client()
    log = log if log is not None else get_logger(__name__)

    session = api.beta.sessions.create(
        agent=agent_ref(cfg),
        environment_id=_required(cfg, "managed_agents.environment_id"),
        title=title,
    )
    session_id = _field(session, "id")
    if not session_id:
        raise ResearchError("セッション ID が返ってこなかった")

    trace = trace_url(cfg, session_id)
    log = log.bind(session_id=session_id)
    if trace and cfg.get("observability.log_session_trace_url", True):
        log.info("research セッションを作成した", extra={"trace_url": trace})

    def send_prompt() -> None:
        """stream-first。ストリームが開いてから最初の user.message を送る。"""
        api.beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
        )

    deadline = time.monotonic() + float(cfg.get("limits.research_timeout_seconds", 660))
    article: dict[str, Any] | None = None
    usage = dict.fromkeys(USAGE_KEYS, 0)

    for event in _events(api, session_id, on_open=send_prompt, log=log, deadline=deadline):
        kind = _field(event, "type", "")

        if kind == "agent.custom_tool_use":
            if _field(event, "name") != SUBMIT_TOOL:
                continue
            if article is None:
                article = _tool_input(event)
            else:
                # 1 セッション 1 回と system prompt で言ってある。破られたら最初を採る
                log.warning("submit_article が 2 回以上呼ばれた。最初の 1 回を使う")
            _acknowledge(api, session_id, _field(event, "id"))

        elif kind == "span.model_request_end":
            _add_usage(usage, _field(event, "model_usage"))

        elif kind == "session.error":
            # エラーで即終わりとは限らない。記録だけして終端イベントを待つ
            log.error("session.error", extra={"detail": _describe(event)})

        elif kind == "session.status_terminated":
            break

        elif kind == "session.status_idle":
            if _field(_field(event, "stop_reason"), "type") != "requires_action":
                break

    if article is None:
        raise ResearchError(f"submit_article が呼ばれないままセッションが終わった: {session_id}")

    log.info("submit_article を受け取った", extra={"usage": usage})
    return ResearchResult(
        article=article,
        session_id=session_id,
        agent_version=_agent_version(session, cfg),
        usage=usage,
        trace_url=trace,
    )


# ---------------------------------------------------------------------------
# イベントの受け口
# ---------------------------------------------------------------------------


def _events(
    api: Any,
    session_id: str,
    *,
    on_open: Callable[[], None],
    log: Any,
    deadline: float,
    max_reconnects: int = MAX_RECONNECTS,
) -> Iterator[Any]:
    """イベントを 1 つずつ返す。切断したら履歴で穴を埋めてから live に合流する。

    重複排除は event id で行う。id が無いイベント (interrupt など) は
    素通しする — 取りこぼすより二重に見えるほうが安全。
    """
    seen: set[str] = set()
    opened = False
    reconnects = 0

    while True:
        try:
            with api.beta.sessions.events.stream(session_id) as stream:
                if not opened:
                    opened = True  # 送信に失敗しても二度は送らない
                    try:
                        on_open()
                    except Exception as exc:
                        raise ResearchError(f"最初の user.message を送れなかった: {exc}") from exc
                else:
                    for event in _history(api, session_id):
                        if _is_new(seen, event):
                            yield event
                for event in stream:
                    _check_deadline(deadline, session_id)
                    if _is_new(seen, event):
                        yield event
        except ResearchError:
            raise
        except Exception as exc:
            reconnects += 1
            if reconnects > max_reconnects:
                raise ResearchError(
                    f"ストリームを {max_reconnects} 回張り直しても続かなかった: {session_id}"
                ) from exc
            log.warning(
                "ストリームが切れた。張り直して履歴で埋める",
                extra={"attempt": reconnects, "error": str(exc)},
            )
            continue

        # 終端イベントを出さずに閉じた。呼び出し側が break していればここには来ない
        reconnects += 1
        if reconnects > max_reconnects:
            raise ResearchError(f"ストリームが終端イベント無しで閉じ続けた: {session_id}")
        log.warning("ストリームが終端イベント無しで閉じた", extra={"attempt": reconnects})


def _history(api: Any, session_id: str) -> list[Any]:
    """`events.list()` で履歴を取る。SDK 側でページを繰る。"""
    return list(api.beta.sessions.events.list(session_id))


def _is_new(seen: set[str], event: Any) -> bool:
    event_id = _field(event, "id")
    if not event_id:
        return True
    if event_id in seen:
        return False
    seen.add(event_id)
    return True


def _check_deadline(deadline: float, session_id: str) -> None:
    if time.monotonic() > deadline:
        raise ResearchError(f"limits.research_timeout_seconds を超えた: {session_id}")


def _acknowledge(api: Any, session_id: str, tool_use_id: Any) -> None:
    if not tool_use_id:
        raise ResearchError("custom_tool_use にイベント ID が無く、結果を返せない")
    api.beta.sessions.events.send(
        session_id,
        events=[
            {
                "type": "user.custom_tool_result",
                "custom_tool_use_id": tool_use_id,
                "content": [{"type": "text", "text": ACCEPT_MESSAGE}],
            }
        ],
    )


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------


def _default_client() -> Any:
    import anthropic  # Lambda 以外では import 自体を避ける

    return anthropic.Anthropic()


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """SDK のモデルでも dict でも同じように読む。"""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _required(config: Config, dotted: str) -> str:
    value = config.get(dotted, "")
    if not value:
        raise ResearchError(f"{dotted} が空")
    return str(value)


def _as_version(value: Any) -> str | int:
    """環境変数経由だと数値も文字列で来る。数字だけなら int に戻す。"""
    text = str(value)
    return int(text) if text.isdigit() else text


def _agent_version(session: Any, config: Config) -> str | int | None:
    """実際に使われた Agent バージョン。応答が持っていればそちらを信じる。"""
    version = _field(_field(session, "agent"), "version")
    if version is not None:
        return version
    version = config.get("managed_agents.agent_version", None)
    return _as_version(version) if version not in (None, "") else None


def _tool_input(event: Any) -> dict[str, Any]:
    payload = _field(event, "input")
    if not isinstance(payload, Mapping):
        raise ResearchError(f"submit_article の入力がオブジェクトでない: {type(payload).__name__}")
    return dict(payload)


def _add_usage(usage: dict[str, int], model_usage: Any) -> None:
    """`span.model_request_end` の model_usage を積む (`docs/design.md` §10)。

    **キャッシュ分も数える。** セッション内は履歴が使い回されるので、
    input_tokens だけ見ると 1 セッション十数トークンという嘘の数字になり、
    コスト集計 (`digest.cost`) が成り立たない。
    """
    for key in USAGE_KEYS:
        value = _field(model_usage, key, 0)
        if isinstance(value, int):
            usage[key] += value


def _describe(event: Any) -> str:
    error = _field(event, "error")
    return str(_field(error, "message", error) or event)
