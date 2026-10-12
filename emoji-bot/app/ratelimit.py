"""Простой лимитер «N запросов за окно» в памяти (на пользователя и на тип операции)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from .auth import current_user


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, int], deque[float]] = defaultdict(deque)

    def allow(self, bucket: str, user_id: int, limit: int, window: float) -> bool:
        now = time.monotonic()
        hits = self._hits[(bucket, user_id)]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def limited(bucket: str, limit: int, window: float = 60.0):
    async def dependency(request: Request, tg: dict = Depends(current_user)) -> None:
        limiter: RateLimiter = request.app.state.ctx.limiter
        if not limiter.allow(bucket, int(tg["id"]), limit, window):
            raise HTTPException(429, {"code": "rate_limited"})

    return dependency
