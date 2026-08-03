"""入力プロンプトの組み立て (`src/agents/prompt.py`)。

見張っているのは 2 つ。

- **空のブロックを出さないこと。** `<continues>` があるかどうかでエージェントの
  動きが変わる (`docs/design.md` §4-③)。空タグを出すと「探したが無かった」と
  「そもそも渡していない」が区別できなくなる
- **エスケープ。** タイトルにも URL にも `&` や `<` は普通に出てくる
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from agents.prompt import PromptError, render_prompt


def blocks(prompt: str) -> set[str]:
    """最上位のタグ名を集める。"""
    return set(re.findall(r"^<([a-z_]+)", prompt, re.MULTILINE))


def test_full_article_renders_every_block(selected_article: dict[str, Any]) -> None:
    prompt = render_prompt(selected_article)
    assert blocks(prompt) == {"article", "sources", "related_urls", "continues", "excerpt"}


def test_article_block_carries_what_to_research(selected_article: dict[str, Any]) -> None:
    prompt = render_prompt(selected_article)
    assert "<title>Anthropic releases Claude Opus 5</title>" in prompt
    assert "<url>https://www.anthropic.com/news/claude-opus-5</url>" in prompt
    assert "<published_at>2026-07-31T17:02:00Z</published_at>" in prompt
    assert "<category>ai</category>" in prompt


def test_source_attributes_come_in_a_stable_order(selected_article: dict[str, Any]) -> None:
    prompt = render_prompt(selected_article)
    assert (
        '<source kind="hackernews" score="842" num_comments="391" '
        'discussion_url="https://news.ycombinator.com/item?id=44821001" />' in prompt
    )


def test_continues_body_is_the_past_headline(selected_article: dict[str, Any]) -> None:
    """`<continues>` は narrative_ja の出発点。日付と関係を属性で添える。"""
    prompt = render_prompt(selected_article)
    assert '<continues date="2026-07-28" relation="同じ競争の続き">' in prompt
    assert "A社が推論単価を4割下げ" in prompt


@pytest.mark.parametrize(
    ("dropped", "tag"),
    [
        ("sources", "sources"),
        ("related_urls", "related_urls"),
        ("continues", "continues"),
        ("excerpt", "excerpt"),
    ],
)
def test_missing_material_drops_the_whole_block(
    selected_article: dict[str, Any], dropped: str, tag: str
) -> None:
    del selected_article[dropped]
    assert tag not in blocks(render_prompt(selected_article))


def test_empty_material_drops_the_whole_block(selected_article: dict[str, Any]) -> None:
    """None や空リストでも「キーが無い」と同じ扱いにする。"""
    selected_article["sources"] = []
    selected_article["related_urls"] = []
    selected_article["excerpt"] = "   "
    selected_article["continues"] = None
    assert blocks(render_prompt(selected_article)) == {"article"}


def test_no_blank_lines_between_blocks(selected_article: dict[str, Any]) -> None:
    selected_article["sources"] = []
    assert "\n\n" not in render_prompt(selected_article)


def test_markup_in_the_input_is_escaped() -> None:
    prompt = render_prompt(
        {
            "title": 'A & B <script>alert("x")</script>',
            "url": "https://example.com/?a=1&b=2",
            "sources": [{"kind": "other", "discussion_url": 'https://e.com/"q"'}],
        }
    )
    assert "<script>" not in prompt
    assert "A &amp; B &lt;script&gt;" in prompt
    assert "https://example.com/?a=1&amp;b=2" in prompt
    assert "discussion_url='https://e.com/\"q\"'" in prompt


@pytest.mark.parametrize("missing", ["title", "url"])
def test_title_and_url_are_required(missing: str) -> None:
    article = {"title": "t", "url": "https://example.com/"}
    del article[missing]
    with pytest.raises(PromptError):
        render_prompt(article)
