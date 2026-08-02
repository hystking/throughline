# 公開サイト — S3 静的ウェブサイトホスティング + CloudFront カスタムオリジン
#
# ウェブサイトエンドポイントをオリジンにすると S3 側が
# `/2026-08-01/` → `2026-08-01/index.html` のインデックス解決をしてくれるため、
# CloudFront Function が要らない (docs/design.md §7.1 / §14-5)。
#
# トレードオフ: ウェブサイトエンドポイントは OAC / SigV4 に対応しないので、
# バケットは公開読み取りになる。置くのは公開済みの HTML/CSS のみで、
# 中間成果物は別バケット (data、完全プライベート) に分けてある。

# --- S3 ---------------------------------------------------------------------

resource "aws_s3_bucket" "site" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_website_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "404.html"
  }
}

# ACL は使わない。公開はバケットポリシーだけで行う。
resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  # ACL 経由の公開は塞ぐ
  block_public_acls  = true
  ignore_public_acls = true

  # バケットポリシーでの公開読み取りが要るので、この 2 つは false
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "site_public_read" {
  statement {
    sid     = "PublicReadGetObject"
    effect  = "Allow"
    actions = ["s3:GetObject"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    resources = ["${aws_s3_bucket.site.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_public_read.json

  # block_public_policy = false が先に反映されていないと拒否される
  depends_on = [aws_s3_bucket_public_access_block.site]
}

# --- CloudFront -------------------------------------------------------------

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name    = "${var.name_prefix}-security-headers"
  comment = "Throughline のセキュリティヘッダ"

  security_headers_config {
    content_type_options {
      override = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = false
      override                   = true
    }
  }
}

locals {
  origin_id = "${var.name_prefix}-site-website-endpoint"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} public site"
  default_root_object = "index.html"
  price_class         = var.price_class

  origin {
    origin_id = local.origin_id

    # REST エンドポイント (<bucket>.s3.amazonaws.com) ではなく
    # ウェブサイトエンドポイント。インデックス解決のためにこれが要る。
    domain_name = aws_s3_bucket_website_configuration.site.website_endpoint

    custom_origin_config {
      http_port  = 80
      https_port = 443

      # ウェブサイトエンドポイントは HTTPS を話さない
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # 独自ドメインは将来 (docs/design.md §13 F)。今は CloudFront のドメインで見る
    cloudfront_default_certificate = true
  }
}
