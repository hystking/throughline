"""設定ローダ — 環境変数の上書きと `${VAR}` 解決 (docs/design.md §7.6)。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from common.config import (
    CONFIG_PATH_ENV,
    Config,
    ConfigError,
    get_config,
    load_config,
    reset_config_cache,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_YAML = REPO_ROOT / "config" / "pipeline.yaml"

SAMPLE = {
    "version": 1,
    "schedule": {"timezone": "Asia/Tokyo", "publish_at": "07:00", "window_hours": 24},
    "models": {"research": {"id": "claude-opus-5", "effort": "medium", "max_tokens": 16000}},
    "limits": {"articles_max": 12, "articles_min_success_ratio": 0.5},
    "sources": {"rss": {"enabled": True, "feeds": [{"name": "Anthropic", "url": "https://x/"}]}},
    "storage": {"backend": "s3", "data_bucket": "${DATA_BUCKET}", "local_root": "./.local"},
}


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(SAMPLE, allow_unicode=True), encoding="utf-8")
    return path


def load(path: Path, **env: str) -> Config:
    return load_config(path, environ={"DATA_BUCKET": "bucket-from-env", **env})


# --- 素の読み込み ---------------------------------------------------------


def test_reads_yaml_and_exposes_dotted_paths(sample_yaml: Path) -> None:
    config = load(sample_yaml)
    assert config.get("models.research.effort") == "medium"
    assert config["limits.articles_max"] == 12
    assert config.section("schedule")["timezone"] == "Asia/Tokyo"


def test_missing_key_raises_unless_default_given(sample_yaml: Path) -> None:
    config = load(sample_yaml)
    assert config.get("limits.nope", "fallback") == "fallback"
    with pytest.raises(ConfigError, match=re.escape("limits.nope")):
        config.get("limits.nope")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="設定ファイルが無い"):
        load_config(tmp_path / "absent.yaml", environ={})


# --- 環境変数による上書き -------------------------------------------------


def test_env_override_scalar(sample_yaml: Path) -> None:
    config = load(sample_yaml, AINEWS__MODELS__RESEARCH__EFFORT="high")
    assert config.get("models.research.effort") == "high"


def test_env_override_coerces_json_types(sample_yaml: Path) -> None:
    config = load(
        sample_yaml,
        AINEWS__LIMITS__ARTICLES_MAX="8",
        AINEWS__LIMITS__ARTICLES_MIN_SUCCESS_RATIO="0.25",
        AINEWS__SOURCES__RSS__ENABLED="false",
        AINEWS__SOURCES__RSS__FEEDS='[{"name": "Go Blog", "url": "https://go.dev/"}]',
    )
    assert config.get("limits.articles_max") == 8
    assert config.get("limits.articles_min_success_ratio") == 0.25
    assert config.get("sources.rss.enabled") is False
    assert config.get("sources.rss.feeds") == [{"name": "Go Blog", "url": "https://go.dev/"}]


def test_env_override_keeps_non_json_strings_as_strings(sample_yaml: Path) -> None:
    config = load(sample_yaml, AINEWS__SCHEDULE__PUBLISH_AT="06:30")
    assert config.get("schedule.publish_at") == "06:30"


def test_env_override_rejects_unknown_key(sample_yaml: Path) -> None:
    """typo を黙って新しいキーとして通さない。"""
    with pytest.raises(ConfigError, match=re.escape("limits.article_max")):
        load(sample_yaml, AINEWS__LIMITS__ARTICLE_MAX="8")


def test_env_override_rejects_descending_into_scalar(sample_yaml: Path) -> None:
    with pytest.raises(ConfigError, match=re.escape("models.research.effort.deeper")):
        load(sample_yaml, AINEWS__MODELS__RESEARCH__EFFORT__DEEPER="x")


def test_unrelated_env_vars_are_ignored(sample_yaml: Path) -> None:
    config = load(sample_yaml, PATH="/usr/bin", AINEWSNOTAPREFIX="x")
    assert config.get("limits.articles_max") == 12


# --- ${VAR} の解決 --------------------------------------------------------


def test_placeholder_resolved_from_env(sample_yaml: Path) -> None:
    config = load(sample_yaml, DATA_BUCKET="throughline-data-351642")
    assert config.get("storage.data_bucket") == "throughline-data-351642"


def test_placeholder_undefined_fails_at_load(sample_yaml: Path) -> None:
    """実行途中ではなく起動時に落ちること。"""
    with pytest.raises(ConfigError) as exc:
        load_config(sample_yaml, environ={})
    assert "DATA_BUCKET" in str(exc.value)
    assert "storage.data_bucket" in str(exc.value)


def test_placeholder_empty_string_counts_as_undefined(sample_yaml: Path) -> None:
    with pytest.raises(ConfigError, match="DATA_BUCKET"):
        load_config(sample_yaml, environ={"DATA_BUCKET": ""})


def test_override_wins_over_placeholder(sample_yaml: Path) -> None:
    """上書きが先に効くので、${VAR} を用意しなくても値を直接渡せる。"""
    config = load_config(
        sample_yaml,
        environ={"AINEWS__STORAGE__DATA_BUCKET": "direct-bucket"},
    )
    assert config.get("storage.data_bucket") == "direct-bucket"


def test_all_undefined_placeholders_are_reported_at_once(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        yaml.safe_dump({"a": {"one": "${ALPHA}", "two": "${BRAVO}"}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path, environ={})
    assert "ALPHA" in str(exc.value)
    assert "BRAVO" in str(exc.value)


# --- 本物の pipeline.yaml -------------------------------------------------


def test_real_pipeline_yaml_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """リポジトリの config/pipeline.yaml がローダの規則どおりに読めること。"""
    text = PIPELINE_YAML.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text))
    assert placeholders, "pipeline.yaml に ${VAR} が 1 つも無い"

    env = {name: f"dummy-{name.lower()}" for name in placeholders}
    config = load_config(PIPELINE_YAML, environ=env)

    assert config.get("version") == 1
    assert config.get("storage.backend") in {"s3", "local"}
    assert config.get("models.research.id").startswith("claude-")
    assert config.get("managed_agents.researcher_agent_id") == "dummy-agent_id_researcher"
    assert "${" not in str(config.to_dict())

    # 既定のパス探索でも同じファイルに当たること
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reset_config_cache()
    assert get_config().path == PIPELINE_YAML
    reset_config_cache()


def test_config_path_env_overrides_search(
    sample_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(sample_yaml))
    monkeypatch.setenv("DATA_BUCKET", "bucket-from-env")
    reset_config_cache()
    try:
        assert get_config().path == sample_yaml
        assert get_config() is get_config(), "2 回目はキャッシュを返す"
    finally:
        reset_config_cache()
