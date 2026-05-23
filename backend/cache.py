import hashlib
import json
import redis
from backend.config import settings

_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def make_key(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f"{prefix}:{digest}"


def get_json(key: str):
    v = _client.get(key)
    return json.loads(v) if v else None


def set_json(key: str, value, ttl_seconds: int = 120):
    _client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))


def delete_prefix(prefix: str):
    keys = _client.keys(f"{prefix}:*")
    if keys:
        _client.delete(*keys)
