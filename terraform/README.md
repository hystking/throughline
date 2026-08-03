# terraform

Throughline の AWS 構成 (`docs/design.md` §7)。

## 初回セットアップ

認証は環境変数から取る。

```sh
export AWS_PROFILE=gon      # us-east-1 / アカウント 886505351642
```

state バックエンドの S3 バケット (`versions.tf` に直書き) だけは **Terraform の管理外**。
卵が先か鶏が先かを避けるため、`terraform init` の前に手で作る。

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

# 管理外なので provider の default_tags が効かない。手で付ける
aws s3api put-bucket-tagging \
  --bucket throughline-tfstate-351642 \
  --tagging 'TagSet=[{Key=Project,Value=throughline},{Key=ManagedBy,Value=manual}]'
```

> バケット名の `351642` はアカウント ID の下6桁。別アカウントで動かすならこの名前と
> `versions.tf` の `backend "s3"` を書き換える (backend ブロックには変数を書けない)。

## ダミー HTML を置く

`modules/site` はバケットと配信だけを作り、中身は管理しない。
中身は Publish ステージ (issue #11) が入れる。M1 の疎通確認だけ手で置く。

```sh
aws s3 sync terraform/dummy-site \
  "s3://$(terraform -chdir=terraform output -raw site_bucket_name)/" \
  --content-type "text/html; charset=utf-8" \
  --cache-control "public, max-age=60"
```
