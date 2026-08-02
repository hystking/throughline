output "bucket_name" {
  description = "サイト用 S3 バケット名"
  value       = aws_s3_bucket.site.id
}

output "bucket_arn" {
  description = "サイト用 S3 バケットの ARN (Lambda 実行ロールの権限付与に使う / issue #14)"
  value       = aws_s3_bucket.site.arn
}

output "website_endpoint" {
  description = "S3 静的ウェブサイトホスティングのエンドポイント"
  value       = aws_s3_bucket_website_configuration.site.website_endpoint
}

output "cloudfront_domain_name" {
  description = "CloudFront のドメイン名"
  value       = aws_cloudfront_distribution.site.domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront ディストリビューション ID"
  value       = aws_cloudfront_distribution.site.id
}

output "cloudfront_distribution_arn" {
  description = "Invalidation 権限を当該ディストリビューションだけに絞るために使う (issue #14)"
  value       = aws_cloudfront_distribution.site.arn
}
