"""ストレージ抽象 — local / s3 の切り替え (docs/design.md §7.1 / §7.5)。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from common.config import Config
from common.storage import (
    LocalStorage,
    S3Storage,
    StorageError,
    StorageNotFound,
    storage_for,
)


def make_config(**storage: Any) -> Config:
    base = {"backend": "local", "local_root": "./.local", "data_bucket": "", "site_bucket": ""}
    return Config(data={"storage": {**base, **storage}}, path=Path("test.yaml"))


# --- LocalStorage ---------------------------------------------------------


def test_local_round_trip_text_and_json(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write_text("runs/2026-08-01/note.txt", "線で読む")
    store.write_json("runs/2026-08-01/candidates.json", {"items": [{"seq": 1}]})

    assert store.read_text("runs/2026-08-01/note.txt") == "線で読む"
    assert store.read_json("runs/2026-08-01/candidates.json") == {"items": [{"seq": 1}]}
    # 日本語はエスケープせずに書く
    assert "線で読む" in (tmp_path / "runs/2026-08-01/note.txt").read_text(encoding="utf-8")


def test_local_creates_parent_directories(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write_text("a/b/c/deep.txt", "ok")
    assert (tmp_path / "a/b/c/deep.txt").is_file()


def test_local_exists_list_and_delete(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write_text("runs/2026-08-01/a.json", "{}")
    store.write_text("runs/2026-08-02/b.json", "{}")
    store.write_text("state/index.json", "{}")

    assert store.exists("runs/2026-08-01/a.json")
    assert not store.exists("runs/2026-08-03/a.json")
    assert store.list_keys() == [
        "runs/2026-08-01/a.json",
        "runs/2026-08-02/b.json",
        "state/index.json",
    ]
    assert store.list_keys("runs/") == ["runs/2026-08-01/a.json", "runs/2026-08-02/b.json"]

    store.delete("state/index.json")
    assert not store.exists("state/index.json")
    store.delete("state/index.json")  # 2 回目でも落ちない


def test_local_missing_key_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(StorageNotFound):
        LocalStorage(tmp_path).read_text("nope.json")


def test_local_list_keys_on_empty_root(tmp_path: Path) -> None:
    assert LocalStorage(tmp_path / "not-created-yet").list_keys() == []


def test_local_rejects_escaping_root(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path / "root")
    with pytest.raises(StorageError, match="root の外"):
        store.write_text("../outside.txt", "x")


def test_local_rejects_empty_key(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="key が空"):
        LocalStorage(tmp_path).read_text("/")


def test_local_leading_slash_is_stripped(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write_text("/state/index.json", "{}")
    assert store.read_text("state/index.json") == "{}"


def test_local_invalid_json_reports_location(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write_text("broken.json", "{not json")
    with pytest.raises(StorageError, match=re.escape("broken.json")):
        store.read_json("broken.json")


# --- S3Storage (boto3 クライアントを差し替えて検証) ------------------------


class FakeS3:
    """put/get/head/list/delete だけの最小スタブ。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **params: Any) -> None:
        self.puts.append(params)
        self.objects[params["Key"]] = params["Body"]

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _NotFound()
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _NotFound()
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)

    def get_paginator(self, name: str) -> Any:
        objects = self.objects

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str = "") -> Any:
                hits = [{"Key": key} for key in sorted(objects) if key.startswith(Prefix)]
                return [{"Contents": hits}] if hits else [{}]

        return _Paginator()


class _NotFound(Exception):
    response: ClassVar[dict[str, Any]] = {
        "Error": {"Code": "NoSuchKey"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_s3_round_trip_and_metadata() -> None:
    client = FakeS3()
    store = S3Storage("throughline-data", client=client)

    store.write_text(
        "index.html",
        "<html>線</html>",
        content_type="text/html; charset=utf-8",
        cache_control="public, max-age=60",
    )
    assert store.read_text("index.html") == "<html>線</html>"
    assert client.puts[0]["ContentType"] == "text/html; charset=utf-8"
    assert client.puts[0]["CacheControl"] == "public, max-age=60"


def test_s3_write_json_sets_content_type() -> None:
    client = FakeS3()
    S3Storage("b", client=client).write_json("state/index.json", {"days": []})
    assert client.puts[0]["ContentType"] == "application/json; charset=utf-8"
    assert "CacheControl" not in client.puts[0]


def test_s3_exists_list_and_delete() -> None:
    client = FakeS3()
    store = S3Storage("b", client=client)
    store.write_text("runs/a.json", "{}")
    store.write_text("state/index.json", "{}")

    assert store.exists("runs/a.json")
    assert not store.exists("runs/missing.json")
    assert store.list_keys() == ["runs/a.json", "state/index.json"]
    assert store.list_keys("runs/") == ["runs/a.json"]

    store.delete("runs/a.json")
    assert not store.exists("runs/a.json")


def test_s3_missing_key_raises_not_found() -> None:
    with pytest.raises(StorageNotFound, match=re.escape("s3://b/gone.json")):
        S3Storage("b", client=FakeS3()).read_text("gone.json")


def test_s3_requires_bucket_name() -> None:
    with pytest.raises(StorageError, match="バケット名が空"):
        S3Storage("")


# --- storage_for ----------------------------------------------------------


def test_storage_for_local_separates_data_and_site(tmp_path: Path) -> None:
    config = make_config(backend="local", local_root=str(tmp_path / ".local"))

    data = storage_for("data", config)
    site = storage_for("site", config)
    data.write_json("runs/2026-08-01/digest.json", {"ok": True})
    site.write_text("index.html", "<html></html>")

    assert (tmp_path / ".local/data/runs/2026-08-01/digest.json").is_file()
    assert (tmp_path / ".local/site/index.html").is_file()
    assert not data.exists("index.html")


def test_storage_for_s3_picks_the_area_bucket() -> None:
    config = make_config(backend="s3", data_bucket="tl-data", site_bucket="tl-site")
    assert storage_for("data", config).bucket == "tl-data"
    assert storage_for("site", config).bucket == "tl-site"


def test_storage_for_rejects_unknown_area_and_backend() -> None:
    with pytest.raises(StorageError, match="未知の領域"):
        storage_for("logs", make_config())
    with pytest.raises(StorageError, match=re.escape("未知の storage.backend")):
        storage_for("data", make_config(backend="gcs"))
