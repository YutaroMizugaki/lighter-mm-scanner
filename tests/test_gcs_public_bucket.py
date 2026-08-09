"""Unit tests for dual-bucket public JSON upload (mocked GCS client)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lighter_mm.config import Settings, build_storage_backend
from lighter_mm.storage.gcs_backend import GCSStorageBackend


@patch("google.cloud.storage.Client")
def test_public_json_mirrored_to_public_bucket(mock_client_cls: MagicMock, tmp_path: Path) -> None:
    client = MagicMock()
    mock_client_cls.return_value = client
    private_bucket = MagicMock()
    public_bucket = MagicMock()
    private_blob = MagicMock()
    public_blob = MagicMock()
    private_bucket.blob.return_value = private_blob
    public_bucket.blob.return_value = public_blob

    def _bucket(name: str) -> MagicMock:
        return public_bucket if name == "pub-bucket" else private_bucket

    client.bucket.side_effect = _bucket

    backend = GCSStorageBackend(
        "priv-bucket",
        local_root=tmp_path,
        make_public_prefix="lighter-mm/public/",
        public_bucket_name="pub-bucket",
    )
    backend.upload_json("lighter-mm/public/latest.json", {"ok": True}, public=True)

    private_blob.upload_from_string.assert_called_once()
    public_blob.upload_from_string.assert_called_once()
    assert backend.public_https_url("lighter-mm/public/latest.json").endswith(
        "/pub-bucket/lighter-mm/public/latest.json"
    )


@patch("google.cloud.storage.Client")
def test_parquet_stays_on_private_bucket(mock_client_cls: MagicMock, tmp_path: Path) -> None:
    client = MagicMock()
    mock_client_cls.return_value = client
    private_bucket = MagicMock()
    public_bucket = MagicMock()
    private_blob = MagicMock()
    private_bucket.blob.return_value = private_blob

    def _bucket(name: str) -> MagicMock:
        return public_bucket if name == "pub-bucket" else private_bucket

    client.bucket.side_effect = _bucket

    path = tmp_path / "x.parquet"
    path.write_bytes(b"abc")
    backend = GCSStorageBackend(
        "priv-bucket",
        local_root=tmp_path,
        public_bucket_name="pub-bucket",
    )
    backend.upload_file(path, "lighter-mm/runs/r1/trades/x.parquet")
    private_blob.upload_from_filename.assert_called_once()
    public_bucket.blob.assert_not_called()


@patch("google.cloud.storage.Client")
def test_build_storage_backend_reads_unprefixed_public_env(
    mock_client_cls: MagicMock, tmp_path: Path, monkeypatch
) -> None:
    mock_client_cls.return_value = MagicMock()
    monkeypatch.setenv("ENVIRONMENT", "cloud")
    monkeypatch.setenv("GCS_BUCKET", "priv-bucket")
    monkeypatch.setenv("GCS_PUBLIC_BUCKET", "pub-bucket")
    monkeypatch.setenv("GCP_PROJECT_ID", "demo")
    monkeypatch.setenv("TMP_DIR", str(tmp_path))
    # Simulate Settings missing the field while process env has it.
    settings = Settings(environment="cloud", gcs_bucket="priv-bucket", gcs_public_bucket=None)
    backend = build_storage_backend(settings)
    assert isinstance(backend, GCSStorageBackend)
    assert backend.public_bucket_name == "pub-bucket"
