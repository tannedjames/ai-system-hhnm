"""MinIO object storage client."""
import io
import logging
from minio import Minio
from minio.error import S3Error

from backend.config import settings

logger = logging.getLogger(__name__)

# Singleton client
client = Minio(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=settings.MINIO_SECURE,
)


def init_bucket():
    """Create the documents bucket if it doesn't exist."""
    try:
        if not client.bucket_exists(settings.MINIO_BUCKET):
            client.make_bucket(settings.MINIO_BUCKET)
            logger.info(f"Created MinIO bucket: {settings.MINIO_BUCKET}")
        else:
            logger.info(f"MinIO bucket ready: {settings.MINIO_BUCKET}")
    except Exception as e:
        logger.error(f"Failed to init MinIO: {e}")
        raise


def upload_file(filename: str, file_content: bytes) -> str:
    """Upload file content to MinIO. Returns the object name."""
    try:
        client.put_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=filename,
            data=io.BytesIO(file_content),
            length=len(file_content),
        )
        logger.info(f"Uploaded to MinIO: {filename} ({len(file_content)} bytes)")
        return filename
    except S3Error as e:
        logger.error(f"MinIO upload failed: {e}")
        raise


def download_file(filename: str) -> bytes:
    """Download file content from MinIO."""
    try:
        response = client.get_object(settings.MINIO_BUCKET, filename)
        content = response.read()
        response.close()
        response.release_conn()
        return content
    except S3Error as e:
        logger.error(f"MinIO download failed: {e}")
        raise


def delete_file(filename: str):
    """Delete a file from MinIO."""
    try:
        client.remove_object(settings.MINIO_BUCKET, filename)
        logger.info(f"Deleted from MinIO: {filename}")
    except S3Error as e:
        logger.error(f"MinIO delete failed: {e}")
        raise


def list_files() -> list:
    """List all files in the bucket."""
    objects = client.list_objects(settings.MINIO_BUCKET, recursive=True)
    return [
        {
            "name": obj.object_name,
            "size_bytes": obj.size,
            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
        }
        for obj in objects
    ]
