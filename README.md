# Throughline

**テックニュースを、線で読む。**

毎朝5分。テックニュースを「点」ではなく「線」で読めるようにする、日刊ダイジェスト。
Claude Managed Agents が記事ごとに深掘り調査し、その日のダイジェストを静的サイトとして公開する。

- [`docs/product.md`](docs/product.md) — 何を作るのか (PRD)
- [`docs/design.md`](docs/design.md) — どう作るのか (技術設計)
- [`docs/ideas.md`](docs/ideas.md) — 将来の拡張アイデア

タスク台帳は [GitHub Issues](https://github.com/hystking/throughline/issues) が唯一。
何をやるか・どこまでやれば終わりかは issue に書き、ドキュメント側には実装計画を持たない。

## Managed Agents の適用

記事の調査は Claude Managed Agents が行う。Agent と Environment の定義は
`agents/*.yaml` にあり、**`ant` CLI で適用する**。パイプライン側 (Lambda) は
適用済みの ID を環境変数で受け取ってセッションを作るだけで、
`agents.create()` をコードから呼ぶことはない。

```sh
brew install anthropics/tap/ant
xattr -d com.apple.quarantine "$(brew --prefix)/bin/ant"
ant auth login          # または ANTHROPIC_API_KEY を export

make agents-apply       # 初回は create、2 回目以降は update
```

適用結果は `.env.agents` に書き出される (git 管理外)。

```sh
MANAGED_AGENTS_ENVIRONMENT_ID=env_...
AGENT_ID_RESEARCHER=agent_...
AGENT_VERSION_RESEARCHER=3
```

この 3 つと `ANTHROPIC_WORKSPACE_ID` が `config/pipeline.yaml` の
`managed_agents.*` から `${VAR}` で参照される。ローカル実行では
`.env.agents` を読み込み、AWS では Terraform 変数として Lambda の環境変数に渡す。

- **`AGENT_VERSION_RESEARCHER` は毎回控える。** `pin_agent_version: true` の間、
  セッションはこのバージョンに固定される。プロンプトを直したら
  `make agents-apply` → 新しい version を控える、までがワンセット。
- **`agents/researcher.agent.yaml` の `input_schema` は手で書かない。**
  `schemas/article.schema.json` の写しで、`make agents-sync` が同期する。
  ずれていれば `make test` が落ちる。
- `agents/researcher.agent.yaml` の `model` は `config/pipeline.yaml` の
  `models.research` と一致させる。実際に使われるのは Agent 側。
