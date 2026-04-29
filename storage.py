import os
import time

from dotenv import load_dotenv
from minio import Minio

load_dotenv()

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "miniopass")
BUCKET = os.getenv("MINIO_BUCKET", "documents")

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)


def init_bucket():
    for _ in range(10):
        try:
            if not client.bucket_exists(BUCKET):
                client.make_bucket(BUCKET)
            print("MinIO ready")
            return
        except Exception as exc:
            print("Waiting for MinIO...", exc)
            time.sleep(2)

    raise RuntimeError("MinIO not ready")


def bucket_exists() -> bool:
    return client.bucket_exists(BUCKET)


def upload_file(file_path: str, object_name: str) -> str:
    client.fput_object(BUCKET, object_name, file_path)
    return object_name


def download_file(object_name: str, file_path: str):
    client.fget_object(BUCKET, object_name, file_path)


def delete_file(object_name: str):
    client.remove_object(BUCKET, object_name)
