"""JSON Schema とバリデータ (docs/design.md §5)。

正例と負例を対にして、schema の縛りが効いていることを確かめる。
とくに **`narrative_ja` / `insider_ja` / `try_it` が null を通す**ことは
約束①③②の「無理に埋めさせない」を担保する条件なので独立したテストにしてある。
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from common.validate import (
    LAMBDA_ADDED_FIELDS,
    SCHEMA_NAMES,
    ValidationFailed,
    article_tool_input_schema,
    check,
    load_schema,
    schema_dir,
    validate_article,
    validate_digest,
    validate_index,
    validate_stored_article,
)

# --- 正例のひな形 ---------------------------------------------------------


def make_article(**overrides: Any) -> dict[str, Any]:
    article = {
        "title_ja": "Anthropic が Claude Opus 5 を公開",
        "title_original": "Introducing Claude Opus 5",
        "url": "https://www.anthropic.com/news/claude-opus-5",
        "category": "ai",
        "tags": ["LLM", "Anthropic", "推論コスト"],
        "importance": 5,
        "summary_ja": "要約" * 100,
        "key_points": ["1M コンテキスト", "推論単価が据え置き"],
        "narrative_ja": "7 月の値下げ競争の続き。",
        "narrative_refs": [{"what": "A社が推論単価を4割下げ", "when": "2026-07-28"}],
        "actionable_ja": "既存の要約バッチをそのまま載せ替えられる。まず effort を medium で試す。",
        "try_it": {"label": "Playground", "url": "https://platform.claude.com/"},
        "insider_ja": "公表された単価はバッチ利用時のもので、対話用途では実効単価が上がる。",
        "reactions": [
            {
                "platform": "hackernews",
                "author": "someone",
                "quote_ja": "コンテキスト長より単価のほうが効く",
                "url": "https://news.ycombinator.com/item?id=1",
            }
        ],
        "related_links": [
            {
                "title": "Model card",
                "url": "https://www.anthropic.com/model-card",
                "kind": "primary_source",
            }
        ],
        "confidence": "high",
    }
    article.update(overrides)
    return article


def make_stored_article(**overrides: Any) -> dict[str, Any]:
    stored = make_article()
    stored.update(
        {
            "id": "a1b2c3d4e5f6",
            "collected_at": "2026-08-01T21:30:00Z",
            "session_id": "ses_01ABC",
            "agent_version": 3,
            "source_mentions": [
                {"source": "hackernews", "url": "https://news.ycombinator.com/item?id=1"}
            ],
            "usage": {"input_tokens": 51000, "output_tokens": 2400},
        }
    )
    stored.update(overrides)
    return stored


def make_digest(**overrides: Any) -> dict[str, Any]:
    digest = {
        "date": "2026-08-01",
        "generated_at": "2026-08-01T22:14:03Z",
        "headline_ja": "推論コスト競争、一気に前線へ",
        "lead_ja": "本日は…",
        "themes": [
            {
                "title": "推論コストの低下圧力",
                "body_ja": "…",
                "article_ids": ["a1b2c3d4e5f6"],
            }
        ],
        "article_order": ["a1b2c3d4e5f6"],
        "articles": [make_stored_article()],
        "stats": {"candidates": 63, "selected": 12, "researched": 11, "failed": 1},
        "cost": {"input_tokens": 812340, "output_tokens": 41200, "usd_estimate": 4.12},
    }
    digest.update(overrides)
    return digest


def make_index(**overrides: Any) -> dict[str, Any]:
    index = {
        "updated_at": "2026-08-01T22:14:03Z",
        "days": [
            {
                "date": "2026-08-01",
                "headline_ja": "推論コスト競争、一気に前線へ",
                "lead_ja": "本日は…",
                "article_count": 11,
                "articles": [
                    {
                        "id": "a1b2c3d4e5f6",
                        "title_ja": "Anthropic が Claude Opus 5 を公開",
                        "url": "https://www.anthropic.com/news/claude-opus-5",
                        "category": "ai",
                        "tags": ["LLM", "Anthropic"],
                    }
                ],
            },
            # 14 日より古い日は articles[] を落とす (docs/design.md §5.3)
            {
                "date": "2026-06-01",
                "headline_ja": "…",
                "lead_ja": "…",
                "article_count": 9,
            },
        ],
    }
    index.update(overrides)
    return index


# --- schema ファイルそのもの ----------------------------------------------


def test_three_schemas_exist_and_are_valid_json() -> None:
    for name in SCHEMA_NAMES:
        schema = load_schema(name)
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["$id"].endswith(f"{name}.schema.json")


def test_article_schema_matches_categories_in_pipeline_yaml() -> None:
    """site.categories と article schema の enum は 1:1 (config/pipeline.yaml のコメント)。"""
    import yaml

    config = yaml.safe_load(
        (schema_dir().parent / "config" / "pipeline.yaml").read_text(encoding="utf-8")
    )
    assert set(config["site"]["categories"]) == set(
        load_schema("article")["properties"]["category"]["enum"]
    )


def test_article_schema_has_no_lambda_added_fields() -> None:
    """エージェントに書かせないフィールドが input_schema に混ざっていないこと。"""
    properties = load_schema("article")["properties"]
    assert not set(LAMBDA_ADDED_FIELDS) & set(properties)


def test_schemas_are_valid_draft_2020_12() -> None:
    """schema 自体が壊れていないこと。書き間違えた縛りが黙って無効化されるのを防ぐ。"""
    from jsonschema import Draft202012Validator

    for name in SCHEMA_NAMES:
        Draft202012Validator.check_schema(load_schema(name))


def test_tool_input_schema_is_the_article_schema_itself() -> None:
    """custom tool にはこれをそのまま渡す。加工版を作らない (二重管理の禁止)。"""
    assert article_tool_input_schema() == load_schema("article")


def test_tool_input_schema_is_a_copy() -> None:
    """呼び出し側が書き換えてもキャッシュ (= 検証に使う schema) を壊さない。"""
    article_tool_input_schema()["properties"].pop("url")
    assert "url" in load_schema("article")["properties"]


# --- Article: 正例 --------------------------------------------------------


def test_article_full_example_passes() -> None:
    validate_article(make_article())


def test_article_minimal_example_passes() -> None:
    """required だけの記事。任意フィールドは無くても通る。"""
    validate_article(
        {
            "title_ja": "タイトル",
            "url": "https://example.com/a",
            "category": "dev",
            "importance": 3,
            "summary_ja": "要約",
            "key_points": ["a", "b"],
            "actionable_ja": "こうする",
            "confidence": "medium",
        }
    )


@pytest.mark.parametrize("field", ["narrative_ja", "insider_ja", "try_it"])
def test_nullable_fields_accept_null(field: str) -> None:
    """確認できなければ null。無理に埋めさせないための最重要条件。"""
    validate_article(make_article(**{field: None}))


def test_all_three_nullable_fields_null_at_once() -> None:
    validate_article(make_article(narrative_ja=None, insider_ja=None, try_it=None))


# --- Article: 負例 --------------------------------------------------------


def test_article_missing_required_field_fails() -> None:
    article = make_article()
    del article["actionable_ja"]
    with pytest.raises(ValidationFailed) as exc:
        validate_article(article)
    assert "actionable_ja" in str(exc.value)


def test_article_unknown_field_fails() -> None:
    """additionalProperties: false。schema にないものを書かせない。"""
    with pytest.raises(ValidationFailed):
        validate_article(make_article(score=0.9))


def test_article_unknown_category_fails() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_article(make_article(category="ai_agents"))
    assert "category" in str(exc.value)


def test_article_importance_out_of_range_fails() -> None:
    with pytest.raises(ValidationFailed):
        validate_article(make_article(importance=6))


def test_article_key_points_bounds() -> None:
    with pytest.raises(ValidationFailed):
        validate_article(make_article(key_points=["1 つだけ"]))
    with pytest.raises(ValidationFailed):
        validate_article(make_article(key_points=["a", "b", "c", "d", "e"]))


def test_article_tags_bounds() -> None:
    with pytest.raises(ValidationFailed):
        validate_article(make_article(tags=["1 つ", "2 つ"]))
    with pytest.raises(ValidationFailed):
        validate_article(make_article(tags=["a", "b", "c", "d", "e", "f"]))


def test_article_reactions_require_url() -> None:
    """反応には必ず出典を付けさせる。創作された反応を混ぜさせないため。"""
    with pytest.raises(ValidationFailed) as exc:
        validate_article(make_article(reactions=[{"platform": "hackernews", "quote_ja": "すごい"}]))
    assert "url" in str(exc.value)


def test_article_try_it_requires_both_label_and_url() -> None:
    with pytest.raises(ValidationFailed):
        validate_article(make_article(try_it={"url": "https://example.com/"}))


def test_article_narrative_refs_capped_at_three() -> None:
    ref = {"what": "何か", "when": "2026-07-01"}
    with pytest.raises(ValidationFailed):
        validate_article(make_article(narrative_refs=[ref] * 4))


def test_summary_ja_must_be_string_not_list() -> None:
    with pytest.raises(ValidationFailed):
        validate_article(make_article(summary_ja=["a", "b"]))


# --- Article: Lambda 付与フィールド込み ------------------------------------


def test_stored_article_passes() -> None:
    validate_stored_article(make_stored_article())


def test_stored_article_fails_plain_article_schema() -> None:
    """付与フィールドは article schema には無い。剥がして検証する必要がある。"""
    with pytest.raises(ValidationFailed):
        validate_article(make_stored_article())


def test_stored_article_requires_id() -> None:
    stored = make_stored_article()
    del stored["id"]
    with pytest.raises(ValidationFailed) as exc:
        validate_stored_article(stored)
    assert "id" in str(exc.value)


def test_stored_article_id_must_be_sha1_prefix() -> None:
    with pytest.raises(ValidationFailed):
        validate_stored_article(make_stored_article(id="A1B2C3D4E5F6"))  # 大文字は不可
    with pytest.raises(ValidationFailed):
        validate_stored_article(make_stored_article(id="a1b2c3"))  # 12 桁でない


def test_stored_article_still_checks_the_body() -> None:
    """付与フィールドを見るだけで本体の検証が緩むことがないように。"""
    with pytest.raises(ValidationFailed) as exc:
        validate_stored_article(make_stored_article(category="ai_agents"))
    assert "category" in str(exc.value)


def test_stored_article_rejects_non_object() -> None:
    with pytest.raises(ValidationFailed):
        validate_stored_article("記事ではない")


# --- Digest ---------------------------------------------------------------


def test_digest_example_passes() -> None:
    validate_digest(make_digest())


def test_digest_with_single_article_and_no_themes_passes() -> None:
    """記事が 1 本しかない日。束ねる潮流が無くても壊れないこと。"""
    validate_digest(make_digest(themes=[]))


def test_digest_missing_stats_fails() -> None:
    digest = make_digest()
    del digest["stats"]
    with pytest.raises(ValidationFailed) as exc:
        validate_digest(digest)
    assert "stats" in str(exc.value)


def test_digest_missing_cost_fails() -> None:
    digest = make_digest()
    del digest["cost"]
    with pytest.raises(ValidationFailed):
        validate_digest(digest)


def test_digest_rejects_more_than_three_themes() -> None:
    theme = {"title": "t", "body_ja": "b", "article_ids": ["a1b2c3d4e5f6"]}
    with pytest.raises(ValidationFailed):
        validate_digest(make_digest(themes=[theme] * 4))


def test_digest_article_order_must_be_unique() -> None:
    with pytest.raises(ValidationFailed):
        validate_digest(make_digest(article_order=["a1b2c3d4e5f6", "a1b2c3d4e5f6"]))


def test_digest_bad_date_fails() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_digest(make_digest(date="2026/08/01"))
    assert "date" in str(exc.value)


def test_digest_bad_generated_at_fails() -> None:
    """UTC の秒精度 ISO 8601 に固定する。ローカル時刻を混ぜない。"""
    with pytest.raises(ValidationFailed):
        validate_digest(make_digest(generated_at="2026-08-01T22:14:03+09:00"))


def test_digest_article_must_carry_lambda_fields() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_digest(make_digest(articles=[make_article()]))
    assert "articles[0]" in str(exc.value)


# --- Index ----------------------------------------------------------------


def test_index_example_passes() -> None:
    validate_index(make_index())


def test_index_empty_days_passes() -> None:
    """初回実行。まだ 1 日も公開していない状態。"""
    validate_index({"updated_at": "2026-08-01T22:14:03Z", "days": []})


def test_index_old_day_without_articles_passes() -> None:
    """14 日より古い日は articles[] を落とす。落としても通ること。"""
    index = make_index()
    assert "articles" not in index["days"][1]
    validate_index(index)


def test_index_day_requires_lead_for_the_feed() -> None:
    """feed.xml が見出し + リード文で作られるので lead_ja は必須 (§5.3)。"""
    index = make_index()
    del index["days"][1]["lead_ja"]
    with pytest.raises(ValidationFailed) as exc:
        validate_index(index)
    assert "lead_ja" in str(exc.value)


def test_index_day_article_category_follows_article_schema() -> None:
    """index 側のカテゴリは article schema の enum を $ref している。"""
    index = make_index()
    index["days"][0]["articles"][0]["category"] = "ai_agents"
    with pytest.raises(ValidationFailed):
        validate_index(index)


def test_index_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationFailed):
        validate_index({**make_index(), "recent": []})


# --- エラーメッセージ ------------------------------------------------------


def test_error_message_points_at_the_field() -> None:
    """どのフィールドが原因か分かること (issue #3 の要件)。"""
    errors = check("digest", make_digest(stats={"candidates": 1}))
    assert errors
    assert all(line.startswith("stats") for line in errors), errors


def test_error_message_uses_index_notation_for_arrays() -> None:
    digest = make_digest()
    digest["themes"][0]["article_ids"] = []
    errors = check("digest", digest)
    # 文言は jsonschema 任せ。先頭の位置表記だけを固定する
    assert len(errors) == 1
    assert errors[0].startswith("themes[0].article_ids: ")


def test_all_errors_are_reported_not_just_the_first() -> None:
    article = make_article()
    del article["title_ja"]
    del article["url"]
    errors = check("article", article)
    assert len(errors) >= 2


def test_validation_failed_message_includes_subject() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_article(make_article(category="ai_agents"), subject="a1b2c3d4e5f6")
    assert "a1b2c3d4e5f6" in str(exc.value)


def test_check_returns_empty_list_on_success() -> None:
    assert check("article", make_article()) == []


def test_unknown_schema_name_raises() -> None:
    with pytest.raises(KeyError):
        load_schema("candidates")


# --- 実データを模した往復 --------------------------------------------------


def test_article_survives_json_round_trip() -> None:
    """S3 に書いて読み直しても検証を通ること (storage.write_json は sort_keys)。"""
    stored = make_stored_article()
    reloaded = json.loads(json.dumps(stored, ensure_ascii=False, sort_keys=True))
    validate_stored_article(reloaded)
    assert reloaded == deepcopy(stored)
