output "site_bucket_name" {
  description = "サイト用 S3 バケット名"
  value       = module.site.bucket_name
}

output "site_website_endpoint" {
  description = "S3 静的ウェブサイトホスティングのエンドポイント (CloudFront のオリジン)"
  value       = module.site.website_endpoint
}

output "cloudfront_domain_name" {
  description = "公開 URL。https://<この値>/ でサイトが見える"
  value       = module.site.cloudfront_domain_name
}

output "cloudfront_distribution_id" {
  description = "Invalidation を打つときに使う (Publish ステージ / issue #11)"
  value       = module.site.cloudfront_distribution_id
}
