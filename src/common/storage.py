"""S3 とローカルファイルを同じインターフェースで扱う。

パイプラインは 2 つの領域を使う (`docs/design.md` §7.1):

- ``data`` — 中間成果物 (`runs/<date>/…`, `state/index.json`)。本番は完全プライベートな S3
- ``site`` — 公開する HTML/CSS。本番は静的ウェブサイトホスティングの S3

``AINEWS__STORAGE__BACKEND=local`` にすると両方が ``storage.local_root``
(既定 ``./.local``) の下のディレクトリに切り替わる。`make run-local` はこれを使う。

key は常に ``runs/2026-08-01/candidates.json`` のような **前置きスラッシュ無しの相対パス**。
バックエンドごとのバケット名やディレクトリはこのモジュールの内側に閉じる。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from common.config import Config, get_config

AREAS = ("data", "site")


class StorageError(Exception):
    """ストレージ操作の失敗。"""


class StorageNotFound(StorageError):
    """key が存在しない。"""


class Storage(ABC):
    """バイト列の read/write だけを実装すれば、text と JSON は基底が面倒を見る。"""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def write_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """``prefix`` 配下の key を昇順で返す。"""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def uri(self, key: str) -> str:
        """ログに出すための人間可読な所在。"""

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_json(self, key: str) -> Any:
        raw = self.read_text(key)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise StorageError(f"JSON として読めない: {self.uri(key)}\n{exc}") from exc

    def write_text(
        self,
        key: str,
        text: str,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        self.write_bytes(
            key,
            text.encode("utf-8"),
            content_type=content_type,
            cache_control=cache_control,
        )

    def write_json(self, key: str, obj: Any, *, cache_control: str | None = None) -> None:
        # 日本語をエスケープしない・キー順を固定する。差分が読めることを優先する。
        text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.write_text(
            key,
            text,
            content_type="application/json; charset=utf-8",
            cache_control=cache_control,
        )


def _normalize(key: str) -> str:
    cleaned = key.strip("/")
    if not cleaned:
        raise StorageError("key が空")
    return cleaned


class LocalStorage(Storage):
    """``root`` 以下の実ファイル。``content_type`` などの S3 メタデータは無視する。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / _normalize(key)).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"root の外を指す key: {key}")
        return path

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFound(self.uri(key)) from exc

    def write_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        keys = (
            str(path.relative_to(self.root).as_posix())
            for path in self.root.rglob("*")
            if path.is_file()
        )
        head = prefix.strip("/")
        return sorted(key for key in keys if not head or key.startswith(head))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def uri(self, key: str) -> str:
        return str(self._path(key))


class S3Storage(Storage):
    """1 バケットぶんの S3。``client`` を渡さなければ boto3 を遅延生成する。"""

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        if not bucket:
            raise StorageError("バケット名が空")
        self.bucket = bucket
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # Lambda 以外では import 自体を避ける

            self._client = boto3.client("s3")
        return self._client

    def read_bytes(self, key: str) -> bytes:
        key = _normalize(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                raise StorageNotFound(self.uri(key)) from exc
            raise
        return response["Body"].read()

    def write_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": _normalize(key), "Body": body}
        if content_type:
            params["ContentType"] = content_type
        if cache_control:
            params["CacheControl"] = cache_control
        self.client.put_object(**params)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_normalize(key))
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def list_keys(self, prefix: str = "") -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix.lstrip("/")):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return sorted(keys)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_normalize(key))

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{_normalize(key)}"


def _is_not_found(exc: Exception) -> bool:
    """botocore の 404 系エラーかどうか。botocore を import せずに判定する。"""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404


def storage_for(area: str, config: Config | None = None) -> Storage:
    """``area`` (``data`` / ``site``) のストレージを設定に従って作る。"""
    if area not in AREAS:
        raise StorageError(f"未知の領域: {area} (使えるのは {', '.join(AREAS)})")

    cfg = config if config is not None else get_config()
    backend = cfg.get("storage.backend")

    if backend == "s3":
        return S3Storage(cfg.get(f"storage.{area}_bucket"))
    if backend == "local":
        return LocalStorage(Path(cfg.get("storage.local_root", "./.local")) / area)
    raise StorageError(f"未知の storage.backend: {backend!r} (s3 か local)")
