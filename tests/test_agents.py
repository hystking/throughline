"""Managed Agents の定義ファイル (`agents/*.yaml`)。

このファイルたちは `ant beta:agents create < agents/researcher.agent.yaml` で
そのまま適用される。適用は手作業なので、**壊れていることに気づくのが
API を叩いた後**になりやすい。ここで先に落とす。

見張っているのは 3 つのずれ:

- system prompt が `docs/design.md` §4-③ とずれる (issue #4 の完了条件)
- model / effort が `config/pipeline.yaml` の `models.research` とずれる
  (二重管理。実際に使われるのは Agent 側)
- `input_schema` が `schemas/article.schema.json` とずれる
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from common.validate import article_tool_input_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_YAML = REPO_ROOT / "agents" / "researcher.agent.yaml"
ENVIRONMENT_YAML = REPO_ROOT / "agents" / "research.environment.yaml"
DESIGN_MD = REPO_ROOT / "docs" / "design.md"


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agent() -> dict[str, Any]:
    return load(AGENT_YAML)


@pytest.fixture(scope="module")
def environment() -> dict[str, Any]:
    return load(ENVIRONMENT_YAML)


def design_yaml_block(needle: str) -> str:
    """`docs/design.md` の ```yaml ブロックのうち ``needle`` を含むものを返す。"""
    blocks = re.findall(r"```yaml\n(.*?)```", DESIGN_MD.read_text(encoding="utf-8"), re.DOTALL)
    matched = [block for block in blocks if needle in block]
    assert len(matched) == 1, f"{needle} を含む yaml ブロックが {len(matched)} 個ある"
    return matched[0]


def system_prompt_of(text: str) -> str:
    """``system: |`` から次の最上位キーまでを取り出して字下げを剥がす。

    設計書側のブロックは `input_schema: { ...ARTICLE_SCHEMA... }` という
    プレースホルダを含んでいて YAML として読めない。system だけ切り出す。
    """
    body = re.search(r"^system: \|\n(.*?)(?=^\S)", text, re.DOTALL | re.MULTILINE)
    assert body, "system: | のブロックが見つからない"
    lines = [line[2:] if line.startswith("  ") else line for line in body.group(1).splitlines()]
    return "\n".join(lines).rstrip() + "\n"


# --- Agent ----------------------------------------------------------------


def test_agent_yaml_is_valid_yaml_mapping(agent: dict[str, Any]) -> None:
    assert isinstance(agent, dict)
    assert agent["name"] == "throughline-researcher"


def test_agent_yaml_has_only_fields_the_api_accepts(agent: dict[str, Any]) -> None:
    """`ant` は知らないキーをそのまま送る。typo は API 側の 400 になる。"""
    allowed = {
        "name",
        "model",
        "description",
        "system",
        "tools",
        "mcp_servers",
        "skills",
        "metadata",
    }
    assert set(agent) <= allowed, set(agent) - allowed


def test_agent_model_matches_pipeline_yaml() -> None:
    """`config/pipeline.yaml` の models.research と一致していること。

    ずれても動いてしまい、**設定ファイルのほうを信じて読み違える**のが厄介。
    """
    research = load(REPO_ROOT / "config" / "pipeline.yaml")["models"]["research"]
    agent = load(AGENT_YAML)
    assert agent["model"]["id"] == research["id"]
    assert agent["model"]["effort"] == research["effort"]


def test_agent_system_prompt_matches_design_doc(agent: dict[str, Any]) -> None:
    """issue #4 の完了条件。設計書の system prompt と一字一句同じであること。"""
    assert agent["system"] == system_prompt_of(design_yaml_block("throughline-researcher"))


def test_agent_has_the_builtin_toolset_with_bash_disabled(agent: dict[str, Any]) -> None:
    toolset = next(t for t in agent["tools"] if t["type"] == "agent_toolset_20260401")
    assert toolset["default_config"] == {"enabled": True}
    assert {"name": "bash", "enabled": False} in toolset["configs"]


def test_agent_has_submit_article_custom_tool(agent: dict[str, Any]) -> None:
    tool = next(t for t in agent["tools"] if t.get("name") == "submit_article")
    assert tool["type"] == "custom"
    assert tool["description"]


def test_submit_article_input_schema_is_the_article_schema(agent: dict[str, Any]) -> None:
    """加工版を作らない。エージェントの出力はこの schema でそのまま検証される。"""
    tool = next(t for t in agent["tools"] if t.get("name") == "submit_article")
    assert tool["input_schema"] == article_tool_input_schema()


def test_agent_yaml_is_in_sync_with_the_schema_file() -> None:
    """写しの中身だけでなく、並び順や字下げまで `make agents-sync` の出力どおりか。"""
    result = subprocess.run(
        [sys.executable, "scripts/sync_agent_schema.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# --- Environment ----------------------------------------------------------


def test_environment_yaml_matches_design_doc(environment: dict[str, Any]) -> None:
    designed = yaml.safe_load(design_yaml_block("throughline-research-env"))
    assert environment["name"] == designed["name"]
    assert environment["config"] == designed["config"]


def test_environment_is_cloud_and_unrestricted(environment: dict[str, Any]) -> None:
    """web_fetch の宛先を事前に列挙できないので unrestricted (docs/design.md §4-③)。"""
    assert environment["config"]["type"] == "cloud"
    assert environment["config"]["networking"]["type"] == "unrestricted"


def test_environment_yaml_has_only_fields_the_api_accepts(environment: dict[str, Any]) -> None:
    allowed = {"name", "description", "config", "metadata"}
    assert set(environment) <= allowed, set(environment) - allowed
