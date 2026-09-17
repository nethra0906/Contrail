"""Redis client factory. Used for hot aircraft state (assembler writes,
API reads) and for the WS gateway's viewport pub/sub fanout.
"""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis

from services.common.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=False)
