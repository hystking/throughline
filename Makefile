.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test agents-sync agents-apply

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
