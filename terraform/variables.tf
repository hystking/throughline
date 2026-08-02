variable "region" {
  description = "AWS リージョン。CloudFront 用 ACM 証明書が us-east-1 にしか置けないため、最初からここに寄せる (docs/design.md §7.0)"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "すべての AWS リソース名に付ける接頭辞 (docs/design.md §7.0)"
  type        = string
  default     = "throughline"
}

variable "cloudfront_price_class" {
  description = "CloudFront のプライスクラス"
  type        = string
  default     = "PriceClass_200"
}
