# Throughline

**テックニュースを、線で読む。**

毎朝5分。テックニュースを「点」ではなく「線」で読めるようにする、日刊ダイジェスト。

Claude Managed Agents が記事ごとに深掘り調査し、その日のダイジェストを静的サイトとして公開する。

---

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [`docs/product.md`](docs/product.md) | **プロダクト定義 (PRD)**。何を作るのか、誰のためか、4つの約束。技術の話は書かない |
| [`docs/design.md`](docs/design.md) | **技術設計書**。どう作るのか。アーキテクチャ、データモデル、AWS 構成 |
| [`docs/ideas.md`](docs/ideas.md) | 将来の拡張アイデア置き場 (トレンド機能ほか) |
| [`config/pipeline.yaml`](config/pipeline.yaml) | パイプライン設定。モデル・effort・情報源・本数など |

## 4つの約束

1. **線で読める** — この出来事が「何の続き」なのかがわかる
2. **すぐに活かせる** — 自分の仕事に何が効くのかがわかる
3. **他人に自慢できる** — 人に話したくなる厚みがある
4. **数分で理解できる** — 1本30秒、全体5分

## 構成

```
EventBridge → Step Functions
  ① Collect     各ソースから取得して並べるだけ (加工しない)
  ② Triage      重複・既出・続報・採否をまとめて判断        [Claude]
  ③ Research    記事1本 = Managed Agent セッション1つ      [Claude Managed Agents]
  ④ Synthesize  総括とテーマ束ね                          [Claude]
  ⑤ Publish     Jinja2 → 静的 HTML → S3 + CloudFront
```

各ステージは S3 上の JSON を介して疎結合。任意のステージから手動で再実行できる。

## 実装の進め方

**[GitHub Issues](https://github.com/hystking/throughline/issues) が唯一のタスク台帳。**
何をやるか・どこまでやれば終わりかは issue に書く。ドキュメント側には実装計画を持たない。

マイルストーンは「動くものが見える」単位で切ってある。

| マイルストーン | 何が確かめられるか |
|---|---|
| M1 サイトが見える | CloudFront のドメインでページが開く |
| M2 記事1本が作れる | 検証を通った `article.json` が1本 |
| M3 ローカルで通る | `./.local/site/` をブラウザで開いて読める |
| M4 AWS で動く | 手動起動でエンドツーエンド |
| M5 運用開始 | 毎朝自動で更新される |

## 前提

- AWS リージョン: **`us-east-1`** (北部バージニア)
- 言語: Python 3.13 / インフラ: Terraform
