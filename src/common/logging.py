"""構造化ログ (JSON) を 1 行 1 レコードで stdout に出す。

CloudWatch Logs Insights でフィールドとして引けることが目的
(`docs/design.md` §10)。記事単位の処理では ``article_id`` / ``session_id`` /
``agent_version`` を必ず含めるので、毎回書かずに済むよう
:func:`get_logger` に文脈を束ねられるようにしてある。

    log = get_logger(__name__, article_id="a1b2c3")
    log.info("research 開始", extra={"session_id": "ses_..."})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

# LogRecord が標準で持つ属性。これ以外を「呼び出し側が足した extra」とみなす。
# 手で並べると Python の版が上がったときにずれるので、空のレコードから取る。
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
}


class JsonFormatter(logging.Formatter):
    """1 レコード = 1 行の JSON。extra はトップレベルに展開する。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # 日本語をそのまま読めるようにする。JSON としては ensure_ascii なしでも正しい。
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ContextLogger(logging.LoggerAdapter):
    """束ねた文脈を毎回の extra にマージするアダプタ。"""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        kwargs["extra"] = {**(self.extra or {}), **kwargs.get("extra", {})}
        return msg, kwargs

    def bind(self, **context: Any) -> _ContextLogger:
        """文脈を足した新しいロガーを返す。元のロガーは変えない。"""
        return _ContextLogger(self.logger, {**(self.extra or {}), **context})


def configure_logging(
    config: Any | None = None,
    *,
    level: int | str | None = None,
    stream: IO[str] | None = None,
) -> None:
    """ルートロガーにハンドラを 1 つだけ設定する。何度呼んでも同じ状態になる。

    ``config`` を渡すと ``observability.log_level`` / ``observability.log_format``
    に従う。``log_format: text`` なら人が読む形式に落とす (ローカル用)。
    Lambda が既に付けているハンドラは差し替える。
    """
    log_format = "json"
    if config is not None:
        level = level if level is not None else config.get("observability.log_level", "INFO")
        log_format = config.get("observability.log_format", "json")

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s  %(message)s"))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level if level is not None else logging.INFO)


def get_logger(name: str, **context: Any) -> _ContextLogger:
    """``name`` のロガーに ``context`` を束ねて返す。"""
    return _ContextLogger(logging.getLogger(name), dict(context))
