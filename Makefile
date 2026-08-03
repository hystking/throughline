.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test agents-sync agents-apply research-sample research-one

UV ?= uv

help:  ## このヘルプを出す
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync:  ## 依存を .venv に同期する
	$(UV) sync

lint:  ## ruff で静的チェックと書式チェック
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt:  ## ruff で自動整形と自動修正
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test:  ## pytest
	$(UV) run pytest

# --- Managed Agents (docs/design.md §4-③) -----------------------------------
# Agent / Environment は ant CLI で YAML から適用する (コントロールプレーン)。
# セッション作成は Lambda が SDK で行う (データプレーン)。
# 適用後の ID / version は .env.agents に落ちる (git 管理外)。
# pin_agent_version の間セッションはその version に固定されるので、
# プロンプトを直したら agents-apply → 新しい version を控える、までがワンセット。

agents-sync:  ## agents/researcher.agent.yaml の input_schema を schemas/ に揃える
	$(UV) run python scripts/sync_agent_schema.py

agents-apply:  ## Agent と Environment を適用する (ant beta:agents create|update)
	./scripts/apply-agents.sh

# --- Research 単体 (docs/design.md §4-③ / issue #5) --------------------------
# Triage がまだ無いので、記事 1 本を手で書いて Research だけ回す。
# 本番と同じ API を叩く (課金される)。出力は ./.local/data/ の下。
#
# AINEWS__STORAGE__* を渡しているのは、backend が local でも pipeline.yaml の
# ${DATA_BUCKET} などが解決できないと設定の読み込みごと落ちるため。
# 上書きは ${VAR} の解決より先に効くので、これでバケット名を要求されなくなる。

research-sample:  ## research-one に渡す記事 JSON の雛形を出す
	@$(UV) run python scripts/research_one.py --sample

research-one:  ## 記事 1 本を調査する  ARTICLE=<記事 JSON> [DATE=YYYY-MM-DD]
	@[ -n "$(ARTICLE)" ] || { echo "ARTICLE=<記事 JSON> が要る (雛形: make research-sample)" >&2; exit 1; }
	@set -a; [ -f .env.agents ] && . ./.env.agents; set +a; \
	AINEWS__STORAGE__BACKEND=local \
	AINEWS__STORAGE__DATA_BUCKET=unused \
	AINEWS__STORAGE__SITE_BUCKET=unused \
	AINEWS__STORAGE__CLOUDFRONT_DISTRIBUTION_ID=unused \
	AINEWS__MANAGED_AGENTS__WORKSPACE_ID="$${ANTHROPIC_WORKSPACE_ID:-default}" \
	$(UV) run python scripts/research_one.py "$(ARTICLE)" $(if $(DATE),--date $(DATE),)
