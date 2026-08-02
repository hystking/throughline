# Throughline — 技術設計書

- Status: **レビュー反映済み / 実装待ち** (論点は §14 で決着)
- 最終更新: 2026-08-03
- 前身: `ai-news-collector` (Claude Code Skill + Discord 配信)
- **プロダクト定義: [`docs/product.md`](./product.md) ← 「何を作るか」はこちらが正**
- 関連: [`docs/ideas.md`](./ideas.md) (将来の拡張) / [`config/pipeline.yaml`](../config/pipeline.yaml) (設定)

> 本書は **「どう作るか」** のみを扱う。サービスのコンセプト・提供価値・編集方針は `docs/product.md` を参照。

---

## 1. 概要とゴール

毎日決まった時刻に**テクノロジー全般**のニュースを自動収集し、**Claude Managed Agents** に記事ごとの深掘り調査をさせ、その結果を統合した日次ダイジェストを **S3 + CloudFront の静的サイト**として公開する。

**扱う範囲は AI に限定しない。** AI・開発ツール・クラウド/インフラ・Web・データ・セキュリティ・ハードウェア・業界動向・研究を対象とする (`docs/product.md` §6)。AI が半分弱、それ以外が半分強を目安とし、逸脱は `triage` 設定の警告で検知する。

### 前身からの改善点

| # | 現状 (`ai-news-collector`) | v2 |
|---|---|---|
| 1 | 出力フォーマットが自然言語プロンプト頼み | **JSON Schema で構造化**。テンプレートは schema に対する純粋なレンダリング |
| 2 | 情報源の取得もモデル任せ (`web_fetch` で HN トップページ等) | **時刻ウィンドウを指定した機械的な収集**。Lambda が API を叩いて候補を確定させ、prompt に流し込む |
| 3 | 1 セッションで全部やる | **記事 1 本 = Managed Agent セッション 1 つ**。並列・独立・部分失敗許容 |
| 4 | Discord 投稿 | **静的サイト** (`https://dxxxx.cloudfront.net`) |
| 5 | 実行基盤なし (Claude Code / cron 手動) | **EventBridge + Step Functions + Lambda**、Terraform で IaC 化 |

### 成功条件

- 毎日 JST 07:00 に前日分のダイジェストが自動公開される
- フロントエンド JavaScript ゼロ。HTML + CSS のみ
- 記事 1 本あたり、一次ソース本文 + コミュニティの反応 + 関連情報が要約に反映されている
- 一部の記事の調査が失敗しても、残りは公開される

---

## 2. 非ゴール (v1 スコープ外)

- 独自ドメイン / ACM 証明書 (CloudFront ドメイン直出しで運用)
- 検索・タグ絞り込みなどのインタラクティブ機能 (JS が必要になるため)
- 多言語対応 (日本語のみ)
- 記事本文の全文保存・再配布
- Discord / Slack への同時配信 (将来: Publish ステージにシンクを追加するだけで対応可)

---

## 3. 全体アーキテクチャ

```
                      EventBridge Scheduler (cron: 22:00 UTC = JST 07:00)
                                      │
                                      ▼
                        ┌─────────────────────────────┐
                        │   Step Functions (Standard) │
                        └─────────────────────────────┘
                                      │
   ┌──────────────────────────────────┼─────────────────────────────────────┐
   │                                  │                                     │
   ▼ ①Collect                         ▼ ②Triage                             │
┌──────────┐  HN Algolia API      ┌──────────┐  Messages API                │
│  Lambda  │  Reddit RSS          │  Lambda  │  (structured output)          │
│ collect  │─ RSS/Atom feeds ───▶ │  triage  │─ 候補 ~200本 → 採用 10-12本 ─┤
└──────────┘                      └──────────┘  重複・既出・採否をまとめて判断 │
   │ 取得して並べるだけ (加工しない)        ▲                                 │
   ▼ runs/<date>/candidates.json          │ state/index.json (公開済みの全履歴)  │
                                                                            ▼
                              ┌────────────────── Map (maxConcurrency: 5) ──────────────┐
                              │  ③Research Lambda  (記事 1 本につき 1 実行)              │
                              │      │                                                  │
                              │      │  sessions.create(agent_id, initial_events)       │
                              │      ▼                                                  │
                              │  ┌──────────────────────────────────────────────┐      │
                              │  │  Claude Managed Agents (Anthropic 側で実行)    │      │
                              │  │   - web_search / web_fetch で深掘り            │      │
                              │  │   - 一次ソース本文, HN/Reddit コメント, 関連記事│      │
                              │  │   - custom tool `submit_article` で構造化返却  │      │
                              │  └──────────────────────────────────────────────┘      │
                              │      │  SSE stream で受信 → JSON Schema 検証            │
                              │      ▼ s3://data/runs/<date>/articles/<id>.json         │
                              └─────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼ ④Synthesize
                                         ┌──────────────────────────┐
                                         │  Lambda synthesize       │
                                         │  Managed Agent (editor)  │
                                         │  → 総括 / テーマ束ね / 並び│
                                         └──────────────────────────┘
                                                     │ s3://data/runs/<date>/digest.json
                                                     ▼ ⑤Publish
                                         ┌──────────────────────────┐
                                         │  Lambda publish          │
                                         │  Jinja2 → 静的 HTML       │
                                         └──────────────────────────┘
                                              │                  │
                                              ▼                  ▼
                                     s3://site/            CloudFront Invalidation
                                     (static website hosting)
                                              │
                                              ▼  custom origin
                                     ┌────────────────┐
                                     │   CloudFront   │──▶ 一般公開 (dxxxx.cloudfront.net)
                                     └────────────────┘
```

**設計原則**

1. 各ステージは **S3 上の JSON を介して疎結合**。任意のステージから手動再実行できる
2. **入出力は決定的、判断は LLM。** ① 収集と ⑤ 生成には一切の判断を入れず、
   「取ってくる」「並べて出す」だけにする。判断は ②③④ に集約する
3. **判断を機械的に前処理しない。** 絞り込み・重複排除・スコアリングを Lambda 側でやってから
   LLM に渡すと、二度手間なうえに前処理のほうが誤る。**全部見せて、一度に判断させる**

---

## 4. パイプライン各ステージ

### ① Collect — 機械的な候補収集

**入力**: 実行日時 → 収集ウィンドウ `[前日 06:00 JST, 当日 06:00 JST)` を確定 (24h)

**ソース** (すべて時刻フィルタ付きで機械的に取得。**実際のフィード一覧は `config/pipeline.yaml` の `sources` が正**):

| ソース | 取得方法 | 備考 |
|---|---|---|
| **公式ブログ (RSS/Atom)** 35本 | 各社フィードを直接取得 | **v1 の主力**。一次ソースなので Triage が優先しやすい。AI ラボ (Anthropic / OpenAI / DeepMind / Meta AI / Mistral / HuggingFace / vLLM …)、クラウド・インフラ (AWS / Google Cloud / Cloudflare / Kubernetes / HashiCorp …)、言語・ツール (GitHub / Rust / Go / Python / Node.js)、Web (Chrome / WebKit / Mozilla)、データ (PostgreSQL / DuckDB / ClickHouse)、事例・分析 (Simon Willison / Pragmatic Engineer / Netflix / Stripe) |
| Hacker News | Algolia API `search_by_date?tags=story&numericFilters=created_at_i>{t0},created_at_i<{t1},points>30` | **テック全般の主力ソース**。ページング。`points`/`num_comments` を取得 |
| Reddit ~16サブレ | `https://www.reddit.com/r/<sub>/top/.rss?t=day` + ブラウザ UA | **1 サブレずつ 9 秒間隔 + 429 リトライ必須** (前身 Skill の知見を継承)。AI 系 5 + テック全般 11 (`programming` `devops` `kubernetes` `rust` `golang` `webdev` `netsec` …)。**16 サブレ × 9 秒 ≒ 2.5 分**かかる点に注意 (Collect Lambda の timeout 5 分に収まる範囲) |
| GitHub Trending | `https://github.com/trending?since=daily` スクレイプ | **v1 では無効** (`enabled: false`)。HTML 構造の変化で壊れやすいため |
| ~~X / Twitter~~ | — | **v1 では非対応**。前身は `bird` CLI でリストを取得していたが、Lambda 上に同等の手段がなく X API は有料。その分を公式ブログの拡充で補う。代替案は `docs/ideas.md` §B |

> ⚠️ フィード URL は実装時 (issue #6) に 1 本ずつ疎通確認すること。404 / タイムアウトのフィードは個別にスキップし、全体は継続する。

**加工はしない。** 取得してソースごとに並べ、そのまま書き出すだけ。

URL 正規化・短縮 URL 展開・重複排除・既出判定・キーワードスコアリングは **すべて ② Triage に委ねる**。
機械的に前処理してから LLM に判断させるのは同じ仕事の二度手間で、しかも前処理のほうが判断を誤る
(「同じ発表の別記事」と「続報」は文字列では区別できない)。

各ソースは自前の指標で降順に並べるだけ (HN は points、Reddit は score、RSS は新しい順)。

```jsonc
// runs/<date>/candidates.json
{
  "date": "2026-08-01",
  "window": { "from": "2026-07-31T21:00:00Z", "to": "2026-08-01T21:00:00Z" },
  "candidates": [
    { "seq": 1, "kind": "hackernews", "title": "...", "url": "https://...",
      "published_at": "...", "score": 842, "num_comments": 391,
      "discussion_url": "https://news.ycombinator.com/item?id=..." },
    { "seq": 2, "kind": "rss", "feed": "Cloudflare", "category_hint": "infra",
      "title": "...", "url": "https://...", "published_at": "..." }
    // ...
  ]
}
```

- `seq` は Triage が候補を参照するための通し番号 (LLM に長い URL を復唱させない)
- 件数は素の取得結果しだいで **150〜250 件程度**を見込む。上限 `limits.candidates_max` は暴走時の安全弁

**出力**: `s3://<data-bucket>/runs/<YYYY-MM-DD>/candidates.json`

**失敗方針**: 個別ソースの失敗はログに記録して継続。全ソース失敗時のみステージ失敗。

---

### ② Triage — 選別のすべてを担う (LLM 1 コール)

**Collect の生の候補と「直近14日の公開済み記事」を1リクエストに入れ、重複判定・既出判定・クラスタリング・採否・カテゴリ付与を一度に行う。** エージェントは使わない (ツール不要・単発判断のため)。

**入力**:

| 入力 | 中身 | 用途 |
|---|---|---|
| `runs/<date>/candidates.json` | 候補 150〜250 件 (タイトル / URL / ソース / 指標)。抜粋は入れない | 選別対象 |
| `state/index.json` の直近14日分 | 公開済み記事 (日付 / 見出し / URL / カテゴリ / タグ)。約 170 件 | 既出判定 + **続報の検出** |

合わせて **25〜35k トークン程度**。Opus 5 は 1M コンテキストなので余裕があり、コストも 1 回 $0.2 未満。
**候補を絞ってから渡すより、全部見せたほうが判断が正しくなる。**

- Model: `claude-opus-5`, `effort: medium`
- `output_config.format` で JSON Schema を強制

```jsonc
{
  "selected": [
    {
      "rank": 1,
      "seq_refs": [12, 88, 141],       // 同一トピックとして統合する候補の seq
      "primary_seq": 12,               // 代表 (最も一次に近いもの)
      "category": "infra",
      "importance": 4,
      "reason": "採用理由。1文",
      "continues": {                   // 直近14日に関連する公開済み記事があれば
        "date": "2026-07-28",
        "title": "A社が推論単価を4割下げ",
        "relation": "同じ競争の続き"
      }
    }
  ],
  "dropped_as_published": [            // 既出として落としたもの (監査用)
    { "seq": 33, "published_date": "2026-07-30", "why": "同一記事" }
  ],
  "balance_note": "AI 5 / infra 3 / dev 2 / security 2"
}
```

**システムプロンプトに入れること** (`docs/product.md` §9 の編集方針をそのまま渡す):

1. **重複の統合** — 同じ発表を報じた複数記事は 1 つにまとめ、最も一次に近い URL を `primary_seq` にする
2. **既出の除外** — `index.json` にあるものは落とす。**ただし「続報」は別**。
   「同じ記事の再掲」は落とし、「その後の動き」は採用して `continues` を埋める
   → これが約束①「線で読める」の起点。ここで拾えないと `narrative_ja` が書けない
3. **中身のないものを落とす** — PR記事そのまま、ミーム、煽り、低品質な質問スレッド
4. **カテゴリを偏らせない** — AI に寄せすぎない。開発ツール / インフラ / Web / データ / セキュリティ / 業界動向からも拾う。候補が AI ばかりの日はそのまま AI で埋まってよいが、意図的に偏らせない
5. 採用本数は `limits.articles_max` (既定 12)

**Lambda 側の後処理** (これだけは機械的に):

- `selected` の `seq` を `candidates.json` の実データに引き当てる
- 記事 `id` を確定させる (`primary_seq` の URL の SHA1 先頭12桁)。**id はここで初めて決まる**
- `triage.warn_if_*` (AI が 6 割超 / カテゴリ 3 種未満) に抵触したら**警告ログ**。自動是正はしない

**出力**: `s3://data/runs/<date>/selected.json`

> **なぜ全部 LLM に寄せるのか**
> 「同じ発表の別記事」と「その後の続報」は、URL でもタイトル類似度でも区別できない。
> 前者は落とすべきで、後者は Throughline が最も欲しいものなので、
> **機械的な重複排除は最も価値のあるものを取りこぼす。** ここは判断そのものが仕事。

---

### ③ Research — Managed Agents による深掘り (核心)

**記事 1 本 (= 1 クラスタ) につき Managed Agent セッションを 1 つ起動する。**

#### なぜ Managed Agents か

- エージェントループとサンドボックスを Anthropic 側がホストするため、Lambda 側は「セッションを作る / イベントを受ける」だけで済む。ループ実装・コンテナ管理が不要
- Agent 設定 (system prompt / tools / model) が**バージョン管理された永続オブジェクト**。プロンプト改善のたびに新バージョンが切られ、セッションはバージョンを固定できる → 出力品質の変化を追跡できる
- `web_search` / `web_fetch` を含む組み込みツールセットがそのまま使える

#### Lambda 側の処理フロー

```python
# 疑似コード
session = client.beta.sessions.create(
    agent={"type": "agent", "id": AGENT_ID, "version": AGENT_VERSION},  # 固定バージョン
    environment_id=ENVIRONMENT_ID,
    title=f"research: {article.title[:60]}",
    initial_events=[{"type": "user.message", "content": [{"type": "text", "text": render_prompt(article)}]}],
)
logger.info("trace=https://platform.claude.com/workspaces/%s/sessions/%s", WORKSPACE_ID, session.id)

result = None
with client.beta.sessions.events.stream(session_id=session.id) as stream:
    for event in stream:
        if event.type == "agent.custom_tool_use" and event.name == "submit_article":
            result = event.input                       # ← 構造化された成果物
            client.beta.sessions.events.send(session.id, events=[{
                "type": "user.custom_tool_result",
                "custom_tool_use_id": event.id,
                "content": [{"type": "text", "text": "Accepted. End your turn now."}],
            }])
        elif event.type == "session.status_terminated":
            break
        elif event.type == "session.status_idle":
            if event.stop_reason.type != "requires_action":
                break
        elif event.type == "session.error":
            logger.error(...)

validate(result, ARTICLE_SCHEMA)   # jsonschema で検証
```

> **構造化出力の受け口として custom tool を採用する理由**
> Managed Agents のセッションは「メッセージ + イベント」を返すもので、Messages API の `output_config.format` に相当する仕組みがない。選択肢は 2 つ:
> 1. **custom tool `submit_article`** — `input_schema` で形が保証され、SSE ストリームでそのまま受け取れる ← **採用**
> 2. `/mnt/session/outputs/result.json` に書かせて Files API (`scope_id`) でダウンロード — 追加ラウンドトリップとインデックス遅延 (1-3秒) がある
>
> 1 を主、2 をフォールバックとする。

#### Agent 設定 (`agents/researcher.agent.yaml`)

```yaml
name: throughline-researcher
model:
  id: claude-opus-5
  effort: medium          # config/pipeline.yaml の models.research と一致させること。
                          # 出力を見て high に上げる想定 (§14-3)
description: 単一のニュース記事を深掘り調査し、構造化された日本語要約を返す
system: |
  あなたは Throughline の記者です。Throughline は「テックニュースを、線で読む」ための
  日刊ダイジェストで、読者は現役のソフトウェアエンジニアです。
  与えられた 1 本のニュースを調査し、`submit_article` で結果を提出することが
  あなたの唯一の仕事です。

  対象領域は AI に限りません。開発ツール・言語・クラウド・インフラ・Web・データ・
  セキュリティ・ハードウェア・業界動向・研究、いずれも同じ基準で扱ってください。

  ## Throughline が読者に約束していること
  あなたが書く内容は、すべてこの4つのどれかに奉仕しなければなりません。
  ① 線で読める     — この出来事が「何の続き」なのかがわかる
  ② すぐに活かせる  — 自分の仕事に何が効くのかがわかる
  ③ 自慢できる     — 人に話したくなる厚みがある
  ④ 数分で理解できる — 30秒で読める

  ## 手順 (必ずこの順で)
  1. 記事本体の URL を web_fetch で取得し、本文を読む。取得できない場合は
     web_search でミラーや二次報道を探す。
  2. discussion_url (Hacker News / Reddit) があれば web_fetch し、
     上位コメントから技術的に鋭い指摘・反論・追加情報を拾う。→ ③
  3. web_search で以下を確認する:
     - 一次ソース (公式ブログ / 論文 / リポジトリ) が別にあるか
     - 事実関係の裏取り (数値, ベンチマーク, 発表主体)
     - **この出来事の前に何があったか。直近数週間の関連する動き** → ①
       (例: 競合の同種の発表、前バージョン、これを引き起こした事件)
       入力に `<continues>` があれば、それを起点に裏取りする。無ければゼロから探す。
  4. 調べた内容を統合し `submit_article` を呼ぶ。

  ## 各フィールドの書き方
  - summary_ja (④): 200〜300字。読者が「何が起きたか」を一読で把握できること。
  - key_points (④): 2〜4個。記事に書いてある事実のみ。憶測を混ぜない。
  - narrative_ja (①): この出来事が何の続きなのかを1文で。
      良い例:「7月下旬にA社が打ち出した値下げに、B社が同水準で追随した形」
      悪い例:「業界では競争が激化している」← 具体的な出来事を指していない
      **手順3で具体的な過去の出来事を特定できなかった場合は null を返すこと。**
      根拠は narrative_refs に入れる。
  - actionable_ja (②): 実務にどう効くかを1〜2文。要約の言い換えは禁止。
      「で、どうする」まで書く。影響がない読者が多いなら、正直にそう書いてよい。
  - try_it (②): 今すぐ試せる URL があれば。無ければ null。
  - insider_ja (③): 記事本文に書かれていない前提・数字の読み方・見落とされている点。
      **ひねり出せない場合は null。** 埋めるために憶測を書くことは禁止。
  - reactions (③): 実在するコメントの要旨。引用元 URL を必ず付ける。
  - tags: トピックページの軸になる。表記を安定させる
      (「LLM」と「大規模言語モデル」を混在させない。英語表記を優先)。
  - confidence: 確認できなかった事項があれば下げる。理由は本文に書かず、confidence だけで示す。

  ## 文体
  - 技術用語は原語のまま残してよい (inference, fine-tuning)。無理な訳語は避ける。
  - 断定できることは断定する。曖昧な逃げを書かない。
  - 煽らない。「衝撃」「ヤバい」は使わない。

  ## 禁止事項 (最重要)
  - submit_article を呼ばずにターンを終えること
  - 一次ソースを読まずに要約すること
  - **存在しないコメント・発言を作ること**
  - **記事に存在しない数値・固有名詞を書くこと**
  - narrative_ja / insider_ja を埋めるために憶測を書くこと (null を返すのが正解)
tools:
  - type: agent_toolset_20260401
    default_config: { enabled: true }
    configs:
      - { name: bash, enabled: false }      # スクラッチ不要。攻撃面を減らす
  - type: custom
    name: submit_article
    description: 調査結果を提出する。1 セッションにつき 1 回だけ呼ぶこと。
    input_schema: { ...ARTICLE_SCHEMA...  }  # §5 参照
```

Agent と Environment は **`ant` CLI で YAML から適用**する (コントロールプレーン)。Lambda は `AGENT_ID` / `AGENT_VERSION` / `ENVIRONMENT_ID` を環境変数で受け取ってセッションを作るだけ (データプレーン)。

> `agents.create()` を Lambda 実行のたびに呼ぶのは**アンチパターン**。孤児 Agent が溜まり、バージョニングの意味も失われる。

#### Environment (`agents/researcher.environment.yaml`)

```yaml
name: throughline-research-env
config:
  type: cloud
  networking:
    type: unrestricted    # web_fetch の対象ドメインを事前列挙できないため
```

#### 入力プロンプト (Lambda が機械的に生成)

```xml
<article>
  <title>Anthropic releases Claude Opus 5</title>
  <url>https://www.anthropic.com/news/claude-opus-5</url>
  <published_at>2026-07-31T17:02:00Z</published_at>
  <category>ai</category>
</article>
<sources>
  <source kind="hackernews" score="842" num_comments="391"
          discussion_url="https://news.ycombinator.com/item?id=44821001" />
  <source kind="reddit" subreddit="r/ClaudeAI" score="1203"
          discussion_url="https://www.reddit.com/r/ClaudeAI/comments/xxxx/" />
</sources>
<related_urls>
  <!-- Triage が同一トピックと判定した別記事 -->
  <url>https://techcrunch.com/2026/07/31/...</url>
</related_urls>

<!-- Triage が検出した「続き」。narrative_ja の出発点。無い場合はブロックごと省略 -->
<continues date="2026-07-28" relation="同じ競争の続き">
  A社が推論単価を4割下げ
</continues>

<excerpt>
  ...RSS の summary が取れていれば。無ければ省略...
</excerpt>
```

> `<continues>` があるときは、エージェントは**それを起点に**過去の流れを裏取りして `narrative_ja` を書く。
> 無いときはゼロから web_search で探し、見つからなければ `narrative_ja: null` を返す。
> Triage が下ごしらえをしておくことで、エージェントの探索が当たりやすくなる。

**出力**: `s3://data/runs/<date>/articles/<article_id>.json`

**失敗方針**: 1 記事の失敗は全体を止めない。Step Functions の `Map` 内で `Retry` (2 回, 指数バックオフ) → `Catch` でスキップを記録。**採用本数の 50% 未満しか成功しなかった場合のみ**パイプライン失敗とする。

---

### ④ Synthesize — 日次ダイジェストの編集

成功した記事 JSON をすべて集め、**編集者エージェント** (別 Agent 設定) に 1 セッションで渡す。

- 本日の見出し (`headline_ja`, 30 字程度) と リード文 (`lead_ja`, 3〜4 文)
- **テーマ束ね**: 複数記事にまたがる潮流を 1〜3 個抽出 (例:「推論コスト競争が本格化」)
  → これが約束①「線で読める」の**日次版**。v1.1 のトピックページ (期間をまたぐ版) と対になる
- 掲載順の決定 (importance と話題の連続性を考慮)
- 各記事の `summary_ja` は**書き換えない** (③ の成果物をそのまま使う)。編集者は「並べ方と総括」だけを担当する

ツールは不要 (入力がすべて手元にある) ため、これも**エージェントではなく Messages API + Structured Outputs** で十分。ただし「関連の裏取りをさせたい」なら Managed Agents に寄せる余地あり → §14-4 で論点化。

**出力**: `s3://data/runs/<date>/digest.json`

---

### ⑤ Publish — 静的サイト生成 (LLM 不使用)

1. `digest.json` を読む
2. **`s3://data/state/index.json` を更新** (§5.3)。パイプライン唯一の永続状態
3. Jinja2 でレンダリング:
   - `<date>/index.html` — 当日ページ (新規)
   - `index.html` — 最新日ページ (当日と同内容。`<link rel="canonical">` は `/<date>/` を指す)
   - `archive/index.html` — 全日インデックス (`index.json` から再生成)
   - `feed.xml` — RSS 2.0 (直近 30 日。1 日 = 1 エントリ、見出し + リード文)
   - `assets/style.<contenthash>.css` — 変更時のみ
   - `404.html`, `robots.txt` — 変更時のみ
4. S3 へ `Content-Type` / `Cache-Control` 付きで PutObject
5. CloudFront Invalidation を**変更したパスだけ**発行 (`/`, `/index.html`, `/archive/*`, `/feed.xml`, `/<date>/*`)
   - `/*` を毎日投げると無効化パス数を無駄に消費するため避ける

**Cache-Control**:

| 対象 | 値 |
|---|---|
| HTML | `public, max-age=0, s-maxage=300, must-revalidate` |
| `feed.xml` | `public, max-age=300, s-maxage=600` |
| `assets/*` (ハッシュ付き) | `public, max-age=31536000, immutable` |

**冪等性**: 同じ日に複数回実行しても同じ出力になる。既存ファイルは上書き。

---

## 5. データモデル

### 5.1 Article (custom tool `submit_article` の `input_schema` と同一)

**4つの約束 (`docs/product.md` §4) と 1:1 で対応させる。** 各フィールドがどの約束を担うかを明示する。

```jsonc
{
  "type": "object",
  "additionalProperties": false,
  "required": ["title_ja", "url", "category", "importance",
               "summary_ja", "key_points", "actionable_ja", "confidence"],
  "properties": {
    "title_ja":       { "type": "string", "description": "日本語見出し。40字以内。体言止め可" },
    "title_original": { "type": "string", "description": "原題" },
    "url":            { "type": "string", "description": "最も一次に近いソース URL" },
    "category": {
      "type": "string",
      "description": "config/pipeline.yaml の site.categories と 1:1 対応",
      "enum": ["ai", "dev", "infra", "web", "data",
               "security", "hardware", "business", "research", "other"]
    },
    "tags":       { "type": "array", "minItems": 3, "maxItems": 5, "items": { "type": "string" },
                    "description": "トピックページの軸になる。既知タグを優先し、なければ新規" },
    "importance": { "type": "integer", "enum": [1, 2, 3, 4, 5], "description": "5 が最重要" },

    // --- 約束④「数分で理解できる」 ---
    "summary_ja": { "type": "string", "description": "200〜300字。何が起きたかを一読で把握できること" },
    "key_points": { "type": "array", "minItems": 2, "maxItems": 4, "items": { "type": "string" },
                    "description": "記事に書いてある事実のみ。憶測を混ぜない" },

    // --- 約束①「線で読める」: サイト上の「この流れ」 ---
    "narrative_ja": {
      "type": ["string", "null"],
      "description": "この出来事が何の続きなのかを1文で。過去の関連する動きが確認できなかった場合は null"
    },
    "narrative_refs": {
      "type": "array", "maxItems": 3,
      "description": "narrative_ja の根拠になる過去の出来事",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["what", "when"],
        "properties": {
          "what": { "type": "string", "description": "何が起きたか (30字以内)" },
          "when": { "type": "string", "description": "YYYY-MM-DD または『7月下旬』のような粒度" },
          "url":  { "type": "string" }
        }
      }
    },

    // --- 約束②「すぐに活かせる」: サイト上の「明日から」 ---
    "actionable_ja": {
      "type": "string",
      "description": "実務にどう効くかを1〜2文。要約の言い換えは禁止。『で、どうする』まで書く"
    },
    "try_it": {
      "type": ["object", "null"], "additionalProperties": false,
      "description": "今すぐ試せる場所 (リポジトリ / ドキュメント / プレイグラウンド)。無ければ null",
      "required": ["label", "url"],
      "properties": { "label": { "type": "string" }, "url": { "type": "string" } }
    },

    // --- 約束③「他人に自慢できる」: サイト上の「ここだけの話」 ---
    "insider_ja": {
      "type": ["string", "null"],
      "description": "記事本文に書かれていない前提・数字の読み方・見落とされている点を1〜2文。無理に作らず、無ければ null"
    },
    "reactions": {
      "type": "array", "maxItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["platform", "quote_ja", "url"],
        "properties": {
          "platform": { "type": "string", "enum": ["hackernews", "reddit", "x", "github", "other"] },
          "author":   { "type": "string" },
          "quote_ja": { "type": "string", "description": "コメントの要旨 (日本語, 80字以内)" },
          "url":      { "type": "string" }
        }
      }
    },
    "related_links": {
      "type": "array", "maxItems": 4,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["title", "url"],
        "properties": {
          "title": { "type": "string" }, "url": { "type": "string" },
          "kind":  { "type": "string", "enum": ["primary_source", "paper", "repo", "coverage", "discussion"] }
        }
      }
    },

    "confidence":  { "type": "string", "enum": ["high", "medium", "low"] }
  }
}
```

Lambda 側が付与するフィールド (エージェントには書かせない):
`id` (Triage が選んだ `primary_seq` の URL の SHA1 先頭 12 桁 — **② で確定する**), `collected_at`, `session_id`, `agent_version`, `source_mentions[]` (統合された候補のソース一覧), `usage` (トークン数)

**フィールドと画面表示の対応**:

| 約束 | schema フィールド | サイト上の見出し |
|---|---|---|
| ① 線で読める | `narrative_ja` + `narrative_refs[]` | **この流れ** |
| ② すぐ活かせる | `actionable_ja` + `try_it` | **明日から** |
| ③ 自慢できる | `insider_ja` + `reactions[]` | **ここだけの話** |
| ④ 数分で理解 | `summary_ja` + `key_points[]` | (本文) |

> `narrative_ja` と `insider_ja` は **null を許容する**。無理に埋めさせると創作が始まるため、
> 「確認できなければ null」を system prompt で明示する。null のときはその見出しごと出さない。

### 5.2 Digest

```jsonc
{
  "date": "2026-08-01",
  "generated_at": "2026-08-01T22:14:03Z",
  "headline_ja": "推論コスト競争、一気に前線へ",
  "lead_ja": "本日は…(3〜4文)",
  "themes": [
    { "title": "推論コストの低下圧力", "body_ja": "…", "article_ids": ["a1b2c3", "d4e5f6"] }
  ],
  "article_order": ["a1b2c3", "d4e5f6", "..."],
  "articles": [ /* Article[] */ ],
  "stats": { "candidates": 63, "selected": 12, "researched": 11, "failed": 1 },
  "cost": { "input_tokens": 812340, "output_tokens": 41200, "usd_estimate": 4.12 }
}
```

### 5.3 Index — パイプライン唯一の永続状態

`s3://data/state/index.json`。**日付の降順に並んだ配列 1 本**で、以下 3 つの用途をすべて賄う。

| 用途 | 使うステージ | 読む範囲 |
|---|---|---|
| 既出判定 + **続報の検出** | ② Triage | 先頭 14 日分の `articles[]` |
| アーカイブ一覧の再生成 | ⑤ Publish | 全日の `date` / `headline_ja` / `article_count` |
| RSS フィードの再生成 | ⑤ Publish | 先頭 30 日分の `date` / `headline_ja` / `lead_ja` |

```jsonc
{
  "updated_at": "2026-08-01T22:14:03Z",
  "days": [
    {
      "date": "2026-08-01",
      "headline_ja": "推論コスト競争、一気に前線へ",
      "lead_ja": "本日は…(3〜4文)",
      "article_count": 11,
      // articles[] は直近 14 日 (triage.recent_retention_days) のみ保持。
      // それより古い日はこのキーごと落として、上の 4 フィールドだけ残す。
      "articles": [
        { "id": "a1b2c3", "title_ja": "Anthropic が Claude Opus 5 を公開",
          "url": "https://...", "category": "ai", "tags": ["LLM", "Anthropic"] }
      ]
    },
    { "date": "2026-06-01", "headline_ja": "…", "lead_ja": "…", "article_count": 9 }
  ]
}
```

**サイズ**: 直近14日 × 12件 ≒ 170 エントリ + 過去日のメタ情報のみ。1 年運用しても数百 KB 程度で、
Triage のプロンプトに載るのは先頭 14 日分だけなのでトークン消費も一定。

> **なぜ 1 ファイルにしたか**
> 当初は `manifest.json` (アーカイブ用) と `recent.json` (既出判定用) に分けていたが、
> どちらも「公開済みの日次履歴」であり、保持期間が違うだけだった。
> **日次エントリを 1 本の配列にし、古い日は `articles[]` を落とす**ことで
> 同じデータ構造のまま両方を満たせる。書き込みも 1 回で済み、不整合が起きない。

---

## 6. サイト構成

catnose.me/lab/hackernews-ja を参照した、**1 カラム・カード列挙型**。

```
v1
  /                    最新日のダイジェスト        → s3://site/index.html
  /2026-08-01/         日別ページ                  → s3://site/2026-08-01/index.html
  /archive/            全日一覧                    → s3://site/archive/index.html
  /feed.xml            RSS
  /assets/style.<hash>.css
  /404.html

v1.1 (① を完成させる。docs/product.md §11)
  /trends/             伸びているキーワード
  /topic/<slug>/       キーワード別の時系列 ← ①「線で読める」の本体
```

**URL はディレクトリ形式 (末尾スラッシュ) を採用する。** S3 の**静的ウェブサイトホスティング**をオリジンにすれば、S3 側が `index.html` を解決してくれるため CloudFront Function は不要 (§7.1 参照)。

### ページ構造 (index / 日別 共通)

```
┌───────────────────────────────────────────────┐
│  Throughline           [Trends]  [Archive]    │  ← ヘッダ (固定リンクのみ)
│  テックニュースを、線で読む。                    │
├───────────────────────────────────────────────┤
│  2026年8月1日 (金)                             │
│  # 推論コスト競争、一気に前線へ                  │  ← headline_ja
│  本日は…(リード文 3〜4 文)                      │  ← lead_ja
│                                               │
│  ▸ 今日のテーマ                                │  ← themes (0〜3)  ①
│    ・推論コストの低下圧力 …                     │
├───────────────────────────────────────────────┤
│  ●●●●○  AI・機械学習   #LLM #Anthropic        │  ← importance / category / tags
│  ## Anthropic が Claude Opus 5 を公開          │  ← title_ja
│  (要約 200〜300字)                             │  ← summary_ja           ④
│                                               │
│  ─ ポイント                                    │  ← key_points          ④
│    ・…  ・…  ・…                              │
│                                               │
│  ─ この流れ                                    │  ← narrative_ja        ①
│    7月下旬のA社値下げに追随した形               │
│    ↳ 7/28 A社が推論単価を4割下げ →             │  ← narrative_refs
│                                               │
│  ─ 明日から                                    │  ← actionable_ja       ②
│    …                          [試す →]        │  ← try_it
│                                               │
│  ─ ここだけの話                                │  ← insider_ja          ③
│    …                                          │
│    「…」— HN /u/xxx                            │  ← reactions           ③
│                                               │
│  → 元記事 / HN (842) / Reddit / 論文           │  ← url + related_links │
├───────────────────────────────────────────────┤
│  (記事カードを article_order 順に繰り返し)       │
├───────────────────────────────────────────────┤
│  ← 前日 (7/31)            翌日 (8/2) →        │
│  Throughline · Generated by Claude            │
└───────────────────────────────────────────────┘
```

- **`narrative_ja` / `insider_ja` / `try_it` が null のときは、その見出しごと出さない。**
  空欄を見せるより、無いほうがよい (`docs/product.md` §9「不確かなものは不確かと書く」)
- タグは v1.1 で `/topic/<slug>/` へのリンクになる。v1 では文字列のまま置く
  (後からリンク化するだけで済むよう、`slug` はこの時点で確定させておく)

- **JS ゼロ**。ダークモードは `@media (prefers-color-scheme: dark)` のみで対応
- レスポンシブは `max-width: 44rem` + 相対単位
- `importance` は `●●●●○` のような文字表現 (画像・SVG 不要)
- OGP メタタグは静的に埋め込む (`og:title` = headline_ja)

テンプレートの詳細な文言・CSS は実装時 (issue #10) に詰める。**data schema が確定していれば後から自由に差し替え可能**というのが本設計の要点。

---

## 7. AWS 構成 / Terraform

### 7.0 リージョンと命名規則

**リージョンは `us-east-1` (北部バージニア)。**

将来 独自ドメインを当てるとき、**CloudFront が使う ACM 証明書は `us-east-1` にしか置けない**。
最初からここに寄せておけば、証明書だけ別リージョンという構成にならずに済む。
サイトの配信は CloudFront のエッジが行い、バッチは 1 日 1 回のためユーザー向けレイテンシへの影響はない。

**接頭辞は `throughline`。** すべての AWS リソース名をこれで統一する。

| リソース | 名前 |
|---|---|
| サイト用 S3 バケット | `throughline-site-<account_id 下6桁>` |
| データ用 S3 バケット | `throughline-data-<account_id 下6桁>` |
| ECR リポジトリ | `throughline` |
| Lambda 関数 | `throughline-collect` / `-triage` / `-research` / `-synthesize` / `-publish` |
| Step Functions | `throughline-daily` |
| EventBridge Scheduler | `throughline-daily-trigger` |
| SSM パラメータ | `/throughline/anthropic-api-key` |
| SNS トピック | `throughline-alerts` |
| Managed Agents Agent | `throughline-researcher` |
| Managed Agents Environment | `throughline-research-env` |

S3 バケット名はグローバル一意である必要があるため、AWS アカウント ID の下6桁を接尾辞にする
(Terraform の `data.aws_caller_identity` から導出。手で決めない)。

共通タグ: `Project = throughline`, `ManagedBy = terraform`

> リポジトリのディレクトリ名は `news-collector` のままだが、
> サービス名は **Throughline** で統一する。気になるなら後でリネームする。

### 7.1 リソース一覧

**`modules/site`** — 公開サイト

**S3 静的ウェブサイトホスティング + CloudFront カスタムオリジン** 構成を採る。
S3 のウェブサイトエンドポイントが `/2026-08-01/` → `2026-08-01/index.html` のインデックス解決を行うため、**CloudFront Function は不要**。

| リソース | 設定 |
|---|---|
| `aws_s3_bucket.site` | サイト用バケット |
| `aws_s3_bucket_website_configuration` | `index_document = "index.html"`, `error_document = "404.html"` |
| `aws_s3_bucket_public_access_block` | `block_public_acls = true`, `ignore_public_acls = true`、**`block_public_policy` / `restrict_public_buckets` は `false`** (バケットポリシーでの公開読み取りが必要なため) |
| `aws_s3_bucket_policy` | `s3:GetObject` を `Principal: "*"` に許可。サイトの内容は元々公開情報 |
| `aws_s3_bucket_server_side_encryption_configuration` | SSE-S3 (AES256) |
| `aws_cloudfront_distribution` | **`custom_origin_config`** でウェブサイトエンドポイント (`<bucket>.s3-website-<region>.amazonaws.com`) を指定。`origin_protocol_policy = "http-only"` (ウェブサイトエンドポイントは HTTPS 非対応)。`default_root_object = "index.html"`, `PriceClass_200`, `viewer_protocol_policy = "redirect-to-https"`, `compress = true`, Managed-CachingOptimized |
| `aws_cloudfront_response_headers_policy` | `X-Content-Type-Options`, `Referrer-Policy`, HSTS |

> **トレードオフ (承知のうえで採用)**
> S3 ウェブサイトエンドポイントは **OAC / SigV4 に対応していない**ため、バケットは公開読み取りにする必要がある。
> つまり CloudFront を経由せず `http://<bucket>.s3-website-....amazonaws.com/` に直接アクセスできる。
> 本サイトの内容はもともと全世界公開であり、実害は「CloudFront のキャッシュとセキュリティヘッダを迂回される」ことのみ。**許容する。**
> なお `data` バケット (中間成果物) は従来どおり**完全プライベート**で、こちらは一切公開しない。
>
> *代替案 (採らない)*: オブジェクトキーを拡張子なし (`2026-08-01`) にして `Content-Type: text/html` を付ければ、
> `/2026-08-01` という URL を REST オリジン + OAC + プライベートバケットのまま実現できる。
> ただし末尾スラッシュ形式にはできず、慣習から外れるため今回は採用しない。

**`modules/pipeline`** — バッチ基盤

| リソース | 設定 |
|---|---|
| `aws_s3_bucket.data` | プライベート。パイプライン中間成果物 (`runs/`, `state/`) |
| `aws_ecr_repository` | Lambda コンテナイメージ用。lifecycle policy で 10 世代保持 |
| `aws_lambda_function` × 5 | `collect` / `triage` / `research` / `synthesize` / `publish`。arm64, コンテナイメージ |
| `aws_iam_role` + policy | S3 (data RW / site RW), `cloudfront:CreateInvalidation`, `ssm:GetParameter`, `kms:Decrypt`, CloudWatch Logs |
| `aws_ssm_parameter.anthropic_api_key` | SecureString。`lifecycle { ignore_changes = [value] }` で値は手動投入 |
| `aws_sfn_state_machine` | Standard。§7.2 |
| `aws_scheduler_schedule` (EventBridge Scheduler) | `cron(0 22 * * ? *)` UTC = JST 07:00。ターゲット = State Machine |
| `aws_cloudwatch_log_group` × 5 | retention 30 日 |
| `aws_sns_topic` + `aws_cloudwatch_metric_alarm` | State Machine `ExecutionsFailed >= 1` でメール通知 |

### 7.2 Step Functions ステートマシン

```
Collect ──▶ Triage ──▶ ResearchMap ──▶ Synthesize ──▶ Publish ──▶ Success
   │           │       (Map, maxConcurrency=5)  │           │
   │           │        └ Retry×2 / Catch→skip  │           │
   └───────────┴──────────────┴─────────────────┴───────────┴──▶ NotifyFailure (SNS)
```

- 各 Lambda timeout: Collect 5min / Triage 3min / Research **12min** / Synthesize 10min / Publish 3min
- Map の `ItemsPath` に `selected.json` の記事配列を渡す。ペイロードが大きくなる場合は S3 参照 (`ItemReader`) に切り替え
- Research の `Catch` は失敗を `{id, error}` として記録し、Synthesize が欠損を許容する

### 7.3 Lambda パッケージング

**コンテナイメージ (ECR, arm64)** を採用。理由: `anthropic` SDK が依存する `pydantic-core` がネイティブバイナリで、macOS からの zip クロスビルドが煩雑なため。

```
Dockerfile (public.ecr.aws/lambda/python:3.13-arm64 ベース)
  └ anthropic, httpx, jinja2, jsonschema, feedparser, boto3
```

5 つの Lambda は**同一イメージ**を共有し、`image_config.command` でハンドラを切り替える (ビルド 1 回で済む)。

### 7.4 ディレクトリ構成 (リポジトリ)

```
news-collector/
├── docs/
│   ├── design.md              ← 本書
│   └── ideas.md               ← 将来の拡張アイデア (トレンド機能ほか)
├── config/
│   ├── pipeline.yaml          ← モデル/effort/情報源/本数などの全設定
│   └── topics.yaml            ← (将来) タグ正規化辞書
├── agents/                    ← Managed Agents 定義 (ant CLI で適用)
│   ├── researcher.agent.yaml
│   └── research.environment.yaml
├── schemas/
│   ├── article.schema.json
│   └── digest.schema.json
├── src/
│   ├── handlers/              ← Lambda エントリポイント
│   │   ├── collect.py
│   │   ├── triage.py
│   │   ├── research.py
│   │   ├── synthesize.py
│   │   └── publish.py
│   ├── collectors/            ← hackernews.py / reddit.py / rss.py
│   ├── agents/                ← Managed Agents クライアントラッパ
│   ├── render/                ← Jinja2 レンダラ
│   └── common/                ← s3.py, config.py, logging.py
├── templates/
│   ├── base.html  day.html  archive.html  feed.xml  404.html
│   └── assets/style.css
├── terraform/
│   ├── main.tf  variables.tf  outputs.tf  versions.tf
│   └── modules/{site,pipeline}/
├── Dockerfile
├── Makefile                   ← build / push / apply / run-local
└── tests/
```

### 7.5 ローカル実行

`make run-local DATE=2026-08-01` で 5 ステージを逐次実行し、S3 の代わりにローカル `./.local/` に書く (`AINEWS__STORAGE__BACKEND=local`)。Managed Agents は本番と同じ API を叩く。テンプレート調整はこれで高速に回す。

### 7.6 設定ファイル `config/pipeline.yaml`

チューニングしたくなる値はすべてコードから追い出し、**1 枚の YAML** に集約する。Lambda コンテナイメージに同梱し、ローカル実行でも同じファイルを読む。

| セクション | 内容 |
|---|---|
| `schedule` | タイムゾーン、公開時刻、収集ウィンドウ幅とラグ |
| `models` | ステージごとの `id` / `effort` / `max_tokens` |
| `limits` | 候補数、記事本数、並列度、タイムアウト、要約文字数など |
| `managed_agents` | Agent ID / Environment ID / バージョン固定の可否 |
| `sources` | HN / Reddit / RSS フィード一覧 / GitHub Trending の有効化 |
| `scoring` | スコアの重み、キーワード辞書、ソース種別の重み |
| `dedupe` | 除去するクエリパラメータ、既出判定の保持日数、タイトル類似度しきい値 |
| `site` | サイトタイトル、カテゴリ表示名、アーカイブ件数 |
| `storage` / `observability` | バケット名、ログレベル、メトリクス |

**上書き規則**: 全キーが環境変数で上書きできる。`AINEWS__<SECTION>__<KEY>` (ネストは `__` 区切り)。
例: `AINEWS__MODELS__RESEARCH__EFFORT=high`, `AINEWS__LIMITS__ARTICLES_MAX=8`
→ **effort やモデルを変えるのに再ビルドもコード変更も不要**。Lambda の環境変数を書き換えるだけで済む。

`${VAR}` 形式の値 (バケット名や Agent ID) は起動時に環境変数から解決し、未定義なら**起動時に失敗させる** (実行途中で気づく事態を避ける)。

> 注意: `models.research` は Managed Agents の Agent 設定 (`agents/researcher.agent.yaml`) と**二重管理**になる。
> セッション作成時に両者を突き合わせ、不一致なら警告ログを出す。Agent 側が実際に使われる値。

---

## 8. 認証・シークレット

| secret | 保管先 | 使う場所 |
|---|---|---|
| `ANTHROPIC_API_KEY` | SSM Parameter Store (SecureString) | Lambda が起動時に 1 回取得しキャッシュ |
| Reddit UA 文字列 | Lambda env var (機密でない) | collect |

- Terraform は Parameter を**空値で作成**し、`ignore_changes = [value]` を付ける。実値は `aws ssm put-parameter` で手動投入 (state に秘密を残さない)
- Lambda 実行ロールは対象 Parameter の `ssm:GetParameter` と、それを暗号化する KMS キーの `kms:Decrypt` のみに限定

---

## 9. 失敗時の挙動・冪等性

| 事象 | 挙動 |
|---|---|
| 個別ソース (例: Reddit 429) の取得失敗 | ログ記録して継続。全ソース失敗時のみ Collect 失敗 |
| 記事 1 本の調査失敗 | Retry×2 → スキップ。`digest.stats.failed` に計上 |
| 調査成功が採用本数の 50% 未満 | パイプライン失敗 (公開しない)。SNS 通知 |
| Managed Agents 429 | SDK の自動リトライ + Step Functions Retry。Map の `maxConcurrency` を下げる |
| セッションが `requires_action` 以外で idle | 正常終了とみなしてストリームを閉じる |
| SSE ストリーム切断 | 再接続時に `events.list()` で履歴を取得し event id で重複排除してから live に合流 |
| Publish 途中失敗 | 再実行で同じ出力になる (冪等)。`digest.json` が残っていれば Publish のみ手動再実行可 |

**再実行**: Step Functions を任意ステージから開始できるよう、各ステージの入力は `{date, stage_input_s3_uri}` のみで完結させる。

---

## 10. 観測性

- **構造化ログ** (JSON) を CloudWatch Logs へ。各記事の処理ログに `article_id` / `session_id` / `agent_version` を必ず含める
- Managed Agents の**トレース URL** (`https://platform.claude.com/workspaces/<ws>/sessions/<id>`) をログ出力 → Console でエージェントの挙動を後から追える
- `span.model_request_end` イベントの `model_usage` を積算し、実行ごとのトークン数・推定コストを `digest.cost` に記録
- CloudWatch カスタムメトリクス: `ArticlesResearched`, `ArticlesFailed`, `PipelineDurationSeconds`, `EstimatedCostUSD`
- アラーム: State Machine 失敗 / 24 時間以内に Publish がない (`AgeOfLatestPublish`)

---

## 11. コスト試算 (概算)

### Anthropic API (最大の費目)

`claude-opus-5` = $5 / $25 per MTok。

| 項目 | 1 日あたり | 備考 |
|---|---|---|
| Triage (1 コール) | ~$0.2 | 候補 150〜250 件 + 直近14日の公開済み記事 = 25〜35k トークン |
| Research × 12 記事 | **$3.0 〜 $6.0** | 1 記事 = 入力 40-80k (web_fetch 本文含む) + 出力 5-10k |
| Synthesize (1 コール) | ~$0.3 | |
| **小計** | **約 $3.5 〜 $6.5 / 日** | 月額 **$105 〜 $195** |

上表は `effort: high` 相当の上限側。**初期設定の `effort: medium` では概ねこの 6〜7 割**を見込む
(月 **$70 〜 $130** 程度)。

コストを動かすつまみ (すべて `config/pipeline.yaml` から、再ビルドなしで変更可):

| つまみ | 設定キー | 効き |
|---|---|---|
| Research の effort | `models.research.effort` | `high`→`medium` で約 3〜4 割減。Opus 5 は低 effort でも品質が高い |
| Research のモデル | `models.research.id` | `claude-sonnet-5` ($3/$15、2026-08-31 まで $2/$10) にすると約 40〜60% 減 |
| 記事本数 | `limits.articles_max` | 12→8 で約 3 割減 |

加えて、Agent の system prompt は 12 セッションで共有されるためプロンプトキャッシュが効く。

> ⚠️ **要確認**: `web_search` / `web_fetch` はサーバーサイドツールとして**別途課金**される可能性がある。Console の料金ページで単価を確認し、上記に加算すること。

### AWS

| 項目 | 月額 |
|---|---|
| Lambda (5 関数 × 30 回、最大 12 分) | < $1 |
| Step Functions Standard (30 実行 × ~20 遷移) | < $0.05 |
| S3 (数百 MB, リクエスト少) | < $0.5 |
| CloudFront (低トラフィック想定) | < $1 (無料枠内の可能性大) |
| ECR / CloudWatch Logs | < $1 |
| **小計** | **月 $3 以下** |

**総額: 月 $110 〜 $200 程度** (API 課金が支配的)。

---

## 12. セキュリティ

- **`data` バケット (中間成果物) は完全プライベート** — パブリックアクセス全ブロック。ここに秘密や未公開データが入る
- **`site` バケットは公開読み取り** — S3 静的ウェブサイトホスティングを使うため (§7.1)。置くのは公開済み HTML/CSS のみで、`data` バケットとは別バケットに分離してある
- Lambda 実行ロールは最小権限 (バケット/プレフィックス単位、CloudFront は当該ディストリビューションのみ)
- API キーは SSM SecureString。Terraform state・ログ・エージェントのコンテナのいずれにも渡らない
- エージェントのサンドボックスから `bash` を無効化 (§3 参照)
- **プロンプトインジェクション**: エージェントは外部 Web ページを読むため、悪意ある指示が混入しうる。対策:
  - エージェントは `submit_article` 以外の副作用を持たない (S3 書き込み等は Lambda 側)
  - Lambda が JSON Schema で厳格に検証し、不正なら破棄
  - HTML 出力時は Jinja2 の自動エスケープを有効化 (URL は `https?://` のみ許可するフィルタを通す)

---

## 13. 将来の拡張余地

**→ `docs/ideas.md` に集約。** 主なもの:

| # | アイデア | 優先度 |
|---|---|---|
| A | **トレンド機能** — キーワード軸でトピックの流れを時系列で追える `/trends/` `/topic/<slug>/` | 高 |
| B | X / Twitter の取り込み (別ジョブで収集し S3 経由で渡す案) | 中 |
| C | 週次まとめ `/weekly/<YYYY-Www>/` | 中 |
| D | Discord / Slack へのシンク | 中 |
| E | Webhook ベースの非同期化 (Lambda の 15 分制約が消える) | 低 |
| F | 独自ドメイン + ACM + Route53 | 低 |
| G | Managed Agents Scheduled Deployments への移行 | 低 (たぶんやらない) |

**A のトレンド機能は v1 のデータ (`article.tags[]` / `digest.themes[]`) だけで実装できる**設計になっている。
Publish ステージに集計とページ生成を足すだけで、新しい LLM 呼び出しも収集処理も要らない。

---

## 14. 決定事項 (2026-08-03 レビュー済み)

| # | 論点 | 決定 |
|---|---|---|
| 14-1 | オーケストレーション | **Step Functions を採用** |
| 14-2 | Triage / Synthesize をエージェント化するか | **しない**。Messages API の単発コールのまま |
| 14-3 | モデルと effort | **`config/pipeline.yaml` で設定可能にする**。初期値は全ステージ `claude-opus-5` / Research は `effort: medium` |
| 14-4 | 情報源 | **X/Twitter は除外**。代わりに**公式ブログ (RSS) を主力**として拡充。記事本数は 12 本 |
| 14-5 | URL 形式 | **ディレクトリ形式 `/2026-08-01/`**。S3 静的ウェブサイトホスティング + CloudFront で CloudFront Function 不要 |
| 14-6 | 記事の重複 | **統合する** (1 クラスタ = 1 セッション = 1 カード)。関連して**トレンド機能**の要望あり → `docs/ideas.md` §A |

### 14-1 の補足

Lambda の実行時間上限は 15 分。記事 12 本を 1 つの Lambda で順に調査すると確実に超える。
Step Functions の `Map` なら記事 1 本 = Lambda 1 実行になり、各実行が上限内に収まる。
加えて記事単位のリトライ・部分失敗が自然に書ける。

### 14-5 の補足 (当初案からの変更)

初稿では「ディレクトリ形式には CloudFront Function が必要」としていたが、**誤り**。
S3 の静的ウェブサイトホスティングをオリジンにすれば S3 側がインデックス解決を行う。
ただし OAC が使えずバケットが公開読み取りになるトレードオフがある (§7.1 参照) — 内容は元々公開情報のため許容。

### 14-3 の補足 — 設定の置き場所

チューニング可能な値はすべて `config/pipeline.yaml` に集約し、環境変数で上書きできるようにした (§7.6)。
effort を上げ下げするのに**コード変更もデプロイも不要**。Lambda の環境変数を書き換えるだけで済む。

---

## 付録 A: 検討したが採用しなかった案

| 案 | 不採用の理由 |
|---|---|
| Claude Agent SDK を Lambda 上で動かす | ハーネスは得られるがデプロイは自前。Managed Agents ならサンドボックスごと Anthropic 側が持つため Lambda が軽くなる |
| 全記事を 1 セッションで調査 | コンテキストが膨らみ品質が落ちる。1 本の失敗が全体に波及する。並列化できない |
| Managed Agents に S3 へ直接書かせる | AWS 認証情報をサンドボックスに渡す必要がある。プロンプトインジェクションの被害範囲が広がる |
| DynamoDB で状態管理 | 1 日 1 回のバッチに対して過剰。S3 上の JSON で十分 |
| Next.js / SSG フレームワーク | 「JS を使わない」要件に対して重い。Jinja2 で十分 |
| GitHub Pages / Vercel | 「S3 + CloudFront」というご要望に沿う |
