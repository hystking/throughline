provider "aws" {
  region = var.region

  # 認証は環境変数 (AWS_PROFILE / AWS_ACCESS_KEY_ID など) から取る。
  # Terraform 側にプロファイル名を焼き込まない。
  default_tags {
    tags = {
      Project   = "throughline"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # S3 バケット名はグローバル一意が要るので、アカウント ID の下6桁を接尾辞にする。
  # 手で決めない (docs/design.md §7.0)。
  account_suffix = substr(
    data.aws_caller_identity.current.account_id,
    length(data.aws_caller_identity.current.account_id) - 6,
    6,
  )
}

module "site" {
  source = "./modules/site"

  bucket_name = "${var.name_prefix}-site-${local.account_suffix}"
  name_prefix = var.name_prefix
  price_class = var.cloudfront_price_class
}
