# terraform

Throughline の AWS 構成。リージョンは **`us-east-1`**、リソース名の接頭辞は **`throughline`**
(`docs/design.md` §7.0)。

```
terraform/
├── versions.tf       provider / backend
├── variables.tf
├── main.tf           provider 設定と modules の呼び出し
├── outputs.tf
├── modules/
│   └── site/         公開サイト (S3 静的ウェブサイトホスティング + CloudFront)
└── dummy-site/       疎通確認用のダミー HTML (issue #11 の Publish が置き換える)
```

## 初回セットアップ

### 1. 認証

```sh
export AWS_PROFILE=gon      # us-east-1 / アカウント 886505351642
aws sts get-caller-identity
```

Terraform にはプロファイル名を焼き込んでいない。認証は環境変数から取る。

### 2. state 用バケットを手で作る

state バックエンドは S3。**このバケットだけは Terraform の管理外**で、
先に手で作る (卵が先か鶏が先かを避けるため)。ロックは S3 ネイティブ
(`use_lockfile = true`) なので DynamoDB テーブルは要らない。

```sh
aws s3api create-bucket \
  --bucket throughline-tfstate-351642 \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket throughline-tfstate-351642 \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket throughline-tfstate-351642 \
  --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

> バケット名の `351642` はアカウント ID の下6桁。別アカウントで動かすなら
> このバケット名と `versions.tf` の `backend "s3"` を書き換える
> (backend ブロックには変数を書けない)。

### 3. apply

```sh
cd terraform
terraform init
terraform plan
terraform apply
```

## ダミー HTML を置く

`modules/site` はバケットと配信だけを作り、**中身は管理しない**。
中身は Publish ステージ (issue #11) が入れる。M1 の疎通確認だけ手で置く。

```sh
BUCKET=$(terraform -chdir=terraform output -raw site_bucket_name)

aws s3 sync terraform/dummy-site "s3://${BUCKET}/" \
  --content-type "text/html; charset=utf-8" \
  --cache-control "public, max-age=60"
```

## 確認

```sh
DOMAIN=$(terraform -chdir=terraform output -raw cloudfront_domain_name)

curl -sI "https://${DOMAIN}/"              # 200 — index.html
curl -sI "https://${DOMAIN}/2026-08-01/"   # 200 — ディレクトリ形式が解決される
curl -sI "https://${DOMAIN}/no-such-page/" # 404 — 404.html が返る
curl -sI "http://${DOMAIN}/"               # 301 → https
```

## 承知のうえのトレードオフ

S3 のウェブサイトエンドポイントは **OAC / SigV4 に対応しない**ため、site バケットは
公開読み取りになる。CloudFront を経由せず `http://<bucket>.s3-website-us-east-1.amazonaws.com/`
に直接届く。中身はもともと全世界公開の HTML/CSS で、実害はキャッシュとセキュリティヘッダの
迂回のみ。**許容する** (`docs/design.md` §7.1 / §12)。

中間成果物を置く data バケット (issue #14) は別バケットで、そちらは完全プライベート。
