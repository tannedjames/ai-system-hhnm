from minio import Minio
from dotenv import load_dotenv
import os
import time

load_dotenv()
client = Minio(
    os.getenv("MINIO_ENDPOINT"),
    access_key=os.getenv("MINIO_ACCESS_KEY"),
    secret_key=os.getenv("MINIO_SECRET_KEY"),
    secure=False
)

BUCKET = os.getenv("MINIO_BUCKET")


def init_bucket():
    for i in range(10):
        try:
            if not client.bucket_exists(BUCKET):
                client.make_bucket(BUCKET)
            print("✅ MinIO ready")
            return
        except Exception as e:
            print("⏳ Waiting for MinIO...", e)
            time.sleep(2)

    raise Exception("❌ MinIO not ready")


def upload_file(file_path, object_name):
    client.fput_object(BUCKET, object_name, file_path)
    return object_name