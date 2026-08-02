variable "bucket_name" {
  description = "サイト用 S3 バケット名 (グローバル一意)"
  type        = string
}

variable "name_prefix" {
  description = "リソース名の接頭辞"
  type        = string
}

variable "price_class" {
  description = "CloudFront のプライスクラス"
  type        = string
  default     = "PriceClass_200"
}
