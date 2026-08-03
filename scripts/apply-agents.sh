#!/usr/bin/env bash
# =============================================================================
#  agents/*.yaml を ant CLI で適用する (issue #4 / docs/design.md §4-③)
# =============================================================================
#  ID が未設定なら create、設定済みなら update。適用後の ID / version を
#  .env.agents に書き出す (gitignore 済み)。Terraform にはここの値を渡す。
#
#      make agents-apply            # 初回は create、2 回目以降は update
#
#  コントロールプレーン (Agent / Environment) は ant、データプレーン
#  (セッション作成) は SDK、という分担。Lambda から agents.create() を
#  呼ばないのはそのため。
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

ANT="${ANT:-ant}"
ENV_FILE=".env.agents"

if ! command -v "$ANT" >/dev/null 2>&1; then
  cat >&2 <<'MSG'
ant CLI が見つからない。

  brew install anthropics/tap/ant
  xattr -d com.apple.quarantine "$(brew --prefix)/bin/ant"
  ant auth login          # または ANTHROPIC_API_KEY を export

MSG
  exit 1
fi

# 前回の適用結果があれば読む (環境変数のほうが優先)
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

# --- Environment -------------------------------------------------------------
if [ -n "${MANAGED_AGENTS_ENVIRONMENT_ID:-}" ]; then
  echo "==> environment update ($MANAGED_AGENTS_ENVIRONMENT_ID)" >&2
  "$ANT" beta:environments update \
    --environment-id "$MANAGED_AGENTS_ENVIRONMENT_ID" \
    >/dev/null <agents/research.environment.yaml
else
  echo "==> environment create" >&2
  MANAGED_AGENTS_ENVIRONMENT_ID=$(
    "$ANT" beta:environments create --transform id -r <agents/research.environment.yaml
  )
fi

# --- Agent -------------------------------------------------------------------
# input_schema が schemas/ とずれたまま適用しない
uv run python scripts/sync_agent_schema.py --check >/dev/null

if [ -n "${AGENT_ID_RESEARCHER:-}" ]; then
  # update は現在の version を要求する (楽観ロック)。取り違えると 409。
  current=$("$ANT" beta:agents retrieve --agent-id "$AGENT_ID_RESEARCHER" --transform version -r)
  echo "==> agent update ($AGENT_ID_RESEARCHER, version $current)" >&2
  AGENT_VERSION_RESEARCHER=$(
    "$ANT" beta:agents update \
      --agent-id "$AGENT_ID_RESEARCHER" \
      --version "$current" \
      --transform version -r <agents/researcher.agent.yaml
  )
else
  echo "==> agent create" >&2
  created=$("$ANT" beta:agents create --transform '{id,version}' --format json <agents/researcher.agent.yaml)
  AGENT_ID_RESEARCHER=$(printf '%s' "$created" | uv run python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  AGENT_VERSION_RESEARCHER=$(printf '%s' "$created" | uv run python -c 'import json,sys; print(json.load(sys.stdin)["version"])')
fi

# --- 控える ------------------------------------------------------------------
cat >"$ENV_FILE" <<EOF
# make agents-apply が書いた。手で書かない。
# config/pipeline.yaml の managed_agents.* がこの値を \${VAR} で参照する。
MANAGED_AGENTS_ENVIRONMENT_ID=$MANAGED_AGENTS_ENVIRONMENT_ID
AGENT_ID_RESEARCHER=$AGENT_ID_RESEARCHER
AGENT_VERSION_RESEARCHER=$AGENT_VERSION_RESEARCHER
EOF

echo >&2
echo "適用した。$ENV_FILE に控えた:" >&2
cat "$ENV_FILE"
