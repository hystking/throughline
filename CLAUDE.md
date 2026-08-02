# CLAUDE.md

## 開発の進め方

**タスクの台帳は GitHub Issues。実装は PR で入れる。** ドキュメントには実装計画を書かない。

### issue

- 着手前に対象の issue を読む (`gh issue view <n>`)
- issue の**完了条件が done の定義**。全部満たすまで終わりではない
- issue のスコープ外には手を出さない。やるべきことを見つけたら別の issue を立てる
- 完了条件を満たせないものが出たら、黙って飛ばさず issue にコメントする

### PR

- **main に直接コミットしない。** issue ごとにブランチを切る (`<issue番号>-<短い説明>`)
- PR の本文に `Closes #<issue番号>` を書き、完了条件を転記してチェックする
- コミットメッセージにも issue 番号を含める (例: `Collect: HN と RSS を実装 (#6)`)
- レビューを受けてからマージする

リポジトリ: https://github.com/hystking/throughline
