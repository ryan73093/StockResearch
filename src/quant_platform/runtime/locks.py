from __future__ import annotations

import logging
from threading import Lock
from uuid import uuid4

logger = logging.getLogger(__name__)


class LocalExecutionLockManager:
    """Process-local lock used by the zero-dependency development environment."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, Lock] = {}
        self._tokens: dict[str, str] = {}

    @property
    def backend(self) -> str:
        return "local"

    def acquire(self, key: str, ttl_seconds: int) -> str | None:
        del ttl_seconds
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        if not lock.acquire(blocking=False):
            return None
        token = uuid4().hex
        with self._guard:
            self._tokens[key] = token
        return token

    def release(self, key: str, token: str) -> None:
        with self._guard:
            if self._tokens.get(key) != token:
                return
            self._tokens.pop(key, None)
            lock = self._locks.get(key)
        if lock and lock.locked():
            lock.release()

    def ping(self) -> bool:
        return True


class RedisExecutionLockManager:
    """Cross-process lock using token-safe Redis SET NX and compare-delete release."""

    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
      return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, url: str) -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("Redis 模式需要安裝 infra 額外套件") from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)

    @property
    def backend(self) -> str:
        return "redis"

    def acquire(self, key: str, ttl_seconds: int) -> str | None:
        token = uuid4().hex
        acquired = self._client.set(f"quant:lock:{key}", token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    def release(self, key: str, token: str) -> None:
        self._client.eval(self._RELEASE_SCRIPT, 1, f"quant:lock:{key}", token)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            logger.exception("Redis health check failed")
            return False


def build_execution_lock_manager(
    redis_url: str, enabled: bool, required: bool
) -> LocalExecutionLockManager | RedisExecutionLockManager:
    if not enabled:
        return LocalExecutionLockManager()
    try:
        manager = RedisExecutionLockManager(redis_url)
        if manager.ping():
            return manager
        raise ConnectionError("Redis ping 失敗")
    except Exception:
        if required:
            raise
        logger.warning("Redis unavailable; falling back to a process-local execution lock")
        return LocalExecutionLockManager()
