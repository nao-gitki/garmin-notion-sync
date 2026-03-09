"""
Google Cloud Storage を使った coaching_memory.md の永続化モジュール。
Cloud Run はステートレスなのでローカルファイルは消えるため GCS に保存する。
"""
import os
from google.cloud import storage

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "run-log-automation-2026-memory")
GCS_MEMORY_BLOB = "coaching_memory.md"

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def download_memory(local_path: str) -> bool:
    """GCS から coaching_memory.md をローカルパスにダウンロード"""
    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(GCS_MEMORY_BLOB)

        if not blob.exists():
            print("  📂 GCS に coaching_memory.md がありません（初回起動）")
            return False

        blob.download_to_filename(local_path)
        print(f"  ☁️  GCS から coaching_memory.md をダウンロードしました")
        return True

    except Exception as e:
        print(f"  ⚠️ GCS ダウンロード失敗: {e}")
        return False


def upload_memory(local_path: str) -> bool:
    """ローカルの coaching_memory.md を GCS にアップロード"""
    if not os.path.exists(local_path):
        print(f"  ⚠️ アップロード対象ファイルが見つかりません: {local_path}")
        return False

    try:
        client = _get_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(GCS_MEMORY_BLOB)
        blob.upload_from_filename(local_path)
        print(f"  ☁️  coaching_memory.md を GCS にアップロードしました")
        return True

    except Exception as e:
        print(f"  ⚠️ GCS アップロード失敗: {e}")
        return False
