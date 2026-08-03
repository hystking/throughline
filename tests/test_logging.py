"""構造化ログ (docs/design.md §10)。"""

from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from common.config import Config
from common.logging import JsonFormatter, configure_logging, get_logger


@pytest.fixture
def stream() -> StringIO:
    return StringIO()


def lines(stream: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


def test_emits_one_json_object_per_record(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    logging.getLogger("collect").info("収集を開始")

    (record,) = lines(stream)
    assert record["level"] == "INFO"
    assert record["logger"] == "collect"
    assert record["message"] == "収集を開始"
    assert record["time"].endswith("+00:00")


def test_extra_fields_are_promoted_to_top_level(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    logging.getLogger("research").info(
        "session 開始", extra={"article_id": "a1b2c3", "session_id": "ses_9"}
    )

    (record,) = lines(stream)
    assert record["article_id"] == "a1b2c3"
    assert record["session_id"] == "ses_9"


def test_japanese_is_not_escaped(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    logging.getLogger("x").info("線で読む")
    assert "線で読む" in stream.getvalue()


def test_exception_is_recorded(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    try:
        raise RuntimeError("調査に失敗")
    except RuntimeError:
        logging.getLogger("research").exception("記事をスキップ")

    (record,) = lines(stream)
    assert "RuntimeError: 調査に失敗" in record["exception"]


def test_non_serializable_extra_falls_back_to_str(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    logging.getLogger("x").info("path", extra={"where": Path("/tmp/a")})
    assert lines(stream)[0]["where"] == "/tmp/a"


def test_get_logger_binds_context_to_every_record(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    log = get_logger("research", article_id="a1b2c3", agent_version="7")
    log.info("開始")
    log.bind(session_id="ses_9").warning("再接続")

    first, second = lines(stream)
    assert first["article_id"] == "a1b2c3" and "session_id" not in first
    assert second["article_id"] == "a1b2c3"
    assert second["session_id"] == "ses_9"
    assert second["agent_version"] == "7"


def test_per_call_extra_overrides_bound_context(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    get_logger("x", article_id="old").info("上書き", extra={"article_id": "new"})
    assert lines(stream)[0]["article_id"] == "new"


def test_configure_logging_reads_the_config(stream: StringIO) -> None:
    config = Config(
        data={"observability": {"log_level": "WARNING", "log_format": "json"}},
        path=Path("test.yaml"),
    )
    configure_logging(config, stream=stream)

    logging.getLogger("x").info("出ない")
    logging.getLogger("x").warning("出る")
    assert [record["message"] for record in lines(stream)] == ["出る"]


def test_text_format_is_not_json(stream: StringIO) -> None:
    config = Config(
        data={"observability": {"log_level": "INFO", "log_format": "text"}},
        path=Path("test.yaml"),
    )
    configure_logging(config, stream=stream)
    logging.getLogger("x").info("読みやすい形")
    assert stream.getvalue().strip().endswith("読みやすい形")
    assert not stream.getvalue().startswith("{")


def test_configure_logging_is_idempotent(stream: StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    configure_logging(level="INFO", stream=stream)
    logging.getLogger("x").info("1 回だけ")
    assert len(lines(stream)) == 1


def test_formatter_can_be_used_standalone() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "msg"
    assert payload["logger"] == "x"
