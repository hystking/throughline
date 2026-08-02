terraform {
  # use_lockfile (S3 ネイティブロック) に 1.10 以降が要る
  required_version = ">= 1.10"

  # state は S3 バックエンド。バケットは Terraform の管理外で、
  # 事前に手で作る (terraform/README.md「初回セットアップ」参照)。
  # backend ブロックには変数を書けないため、値は直書きになる。
  backend "s3" {
    bucket       = "throughline-tfstate-351642"
    key          = "throughline/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
