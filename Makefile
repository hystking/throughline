.DEFAULT_GOAL := help
.PHONY: help sync lint fmt test

UV ?= uv

help:  ## このヘルプを出す
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

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
