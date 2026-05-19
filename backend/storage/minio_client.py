"""MinIO object storage client — production RAG version."""
import hashlib
import io
import logging
import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Generator

from minio import Minio
from minio.commonconfig import ENABLED
from minio.error import S3Error
from minio.versioningconfig import VersioningConfig

from backend.config import settings

logger = logging.getLogger(__name__)

# ✅ Fix 1: Lazy init — không crash khi import
_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    return _client


def init_bucket() -> None:
    """Create the documents bucket if it doesn't exist."""
    client = get_client()
    try:
        if not client.bucket_exists(settings.MINIO_BUCKET):
            client.make_bucket(settings.MINIO_BUCKET)
            # ✅ Fix: Enable versioning để recover file bị xóa nhầm
            client.set_bucket_versioning(
                settings.MINIO_BUCKET,
                VersioningConfig(ENABLED),
            )
            logger.info(f"Created MinIO bucket: {settings.MINIO_BUCKET}")
        else:
            logger.info(f"MinIO bucket ready: {settings.MINIO_BUCKET}")
    except Exception as e:
        logger.error(f"Failed to init MinIO: {e}")
        raise


def _generate_object_name(original_filename: str, content_hash: str) -> str:
    """Generate a unique, collision-proof object name."""
    # ✅ Fix 2: Dùng hash để tránh collision
    ext = Path(original_filename).suffix.lower()
    return f"documents/{content_hash[:16]}{ext}"


def upload_file(
    file_stream: BinaryIO,
    original_filename: str,
    file_size: int,
    user_id: str | None = None,
) -> str:
    """Stream file to MinIO. Returns the generated object name."""
    client = get_client()
    
    # ✅ Fix: Content type cho pipeline chunking đúng loại file
    content_type, _ = mimetypes.guess_type(original_filename)
    content_type = content_type or "application/octet-stream"
    
    # ✅ Fix: Hash nội dung để tạo unique object name
    content = file_stream.read()
    content_hash = hashlib.sha256(content).hexdigest()
    object_name = _generate_object_name(original_filename, content_hash)
    
    # ✅ Fix: Metadata để RAG pipeline biết context
    metadata = {
        "x-amz-meta-original-filename": original_filename,
        "x-amz-meta-content-hash": content_hash,
    }
    if user_id:
        metadata["x-amz-meta-uploaded-by"] = user_id
    
    try:
        client.put_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_name,
            data=io.BytesIO(content),
            length=len(content),
            content_type=content_type,
            metadata=metadata,
        )
        logger.info(f"Uploaded: {object_name} ({len(content)} bytes, {content_type})")
        return object_name
    except S3Error as e:
        logger.error(f"MinIO upload failed: {e}")
        raise


def download_file(object_name: str) -> bytes:
    """Download file content from MinIO."""
    client = get_client()
    try:
        response = client.get_object(settings.MINIO_BUCKET, object_name)
        content = response.read()
        response.close()
        response.release_conn()
        return content
    except S3Error as e:
        logger.error(f"MinIO download failed: {e}")
        raise


def file_exists(object_name: str) -> bool:
    """Check if a file exists in MinIO."""
    # ✅ Fix: Check rõ ràng thay vì để S3Error bung ra
    client = get_client()
    try:
        client.stat_object(settings.MINIO_BUCKET, object_name)
        return True
    except S3Error as e:
        if e.code == "NoSuchKey":
            return False
        raise


def delete_file(object_name: str) -> None:
    """Delete a file from MinIO."""
    client = get_client()
    try:
        client.remove_object(settings.MINIO_BUCKET, object_name)
        logger.info(f"Deleted: {object_name}")
    except S3Error as e:
        logger.error(f"MinIO delete failed: {e}")
        raise


def list_files(
    prefix: str = "documents/",
    max_keys: int = 1000,
) -> Generator[dict, None, None]:
    """List files in bucket — generator to avoid loading all into RAM."""
    # ✅ Fix: Generator + giới hạn để tránh memory bomb
    client = get_client()
    objects = client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
    for i, obj in enumerate(objects):
        if i >= max_keys:
            logger.warning(f"list_files truncated at {max_keys} items")
            break
        yield {
            "name": obj.object_name,
            "size_bytes": obj.size,
            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
        }


def get_presigned_url(
    object_name: str,
    expires: timedelta = timedelta(hours=1),
) -> str:
    """Generate a presigned download URL for frontend direct access."""
    # ✅ New: Frontend download không cần proxy qua backend
    client = get_client()
    return client.presigned_get_object(
        settings.MINIO_BUCKET,
        object_name,
        expires=expires,
    )