---
name: implement
description: Throughline の issue を1本実装して PR を出す。引数で issue 番号を指定でき、省略した場合は優先度の高い open issue を自動で選ぶ。
disable-model-invocation: true
---

# implement

**issue を 1 本だけ実装し、PR を出すまでを行う。**

リポジトリ: `hystking/throughline`

## 1. 対象の issue を決める

引数に issue 番号があればそれを使う (`/implement 6`)。

**引数がなければ自動で選ぶ。** 手順:

```bash
gh issue list -R hystking/throughline --state open --limit 50 \
  --json number,title,milestone,body,labels
```

open な issue のうち、次の順で先頭を選ぶ。

1. **依存が解決済みであること** — body の `依存: #N` に挙がった issue が
   すべて closed。1 つでも open なら候補から外す
2. **マイルストーンが早い順** — `M1` → `M2` → `M3` → `M4` → `M5`
   (マイルストーン未設定は最後)
3. **issue 番号が小さい順**

選んだら、着手する前に**どれを選んだか・なぜそれかをユーザーに 1〜2 行で伝える**。

候補が 1 つも残らない場合 (全部 closed か、依存待ちばかり) は実装を始めず、
その状況を報告して終わる。

## 2. issue を読む

```bash
gh issue view <n> -R hystking/throughline
```

- **完了条件が done の定義。** 全部満たすまで終わりではない
- 参照に挙がっている `docs/` の該当章を読む
- **issue のスコープ外には手を出さない。** やるべきことを見つけたら
  その場で直さず、別の issue を立てる

## 3. ブランチを切る

**main に直接コミットしない。**

```bash
git switch -c <issue番号>-<短い説明>   # 例: 6-collect-hn-rss
```

## 4. 実装する

- コミットメッセージに issue 番号を含める (例: `Collect: HN と RSS を実装 (#6)`)
- 完了条件を 1 つずつ潰す
- **満たせない完了条件が出たら、黙って飛ばさない。**
  issue にコメントするか、PR の「補足」に理由を書く

## 5. PR を出す

```bash
git push -u origin <ブランチ名>
gh pr create -R hystking/throughline --fill
```

PR 本文 (`.github/pull_request_template.md`) には:

- `Closes #<issue番号>`
- **issue の完了条件をそのまま転記してチェックする**
- 確認方法 (どう動かして確かめたか)
- 補足 (レビューで見てほしい点、設計から外れた判断、積み残し)

**マージはしない。** レビューを受けるところまで。

## 参照

| | |
|---|---|
| `docs/product.md` | 何を作るのか。4つの約束と編集方針 |
| `docs/design.md` | どう作るのか |
| `config/pipeline.yaml` | モデル・情報源・本数などの設定 |
