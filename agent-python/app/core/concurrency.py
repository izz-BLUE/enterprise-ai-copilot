"""Bounded admission control for expensive AI request paths."""

import asyncio
from time import perf_counter

from app.core.config import (
    AI_MAX_CONCURRENT_REQUESTS,
    AI_QUEUE_TIMEOUT_MS,
    logger,
)


class ConcurrencyLimitExceeded(RuntimeError):
    """Raised when an AI request cannot obtain a slot before the deadline."""


class RequestConcurrencyLimiter:
    """Single-process async admission controller.

    FastAPI sync handlers run in a worker thread, so limiting at middleware
    level prevents excess requests from occupying that thread pool before the
    retrieval and LLM work starts.
    """

    def __init__(self, max_concurrent: int, queue_timeout_ms: int):
        if max_concurrent < 1:
            raise ValueError('max_concurrent must be >= 1')
        if queue_timeout_ms < 1:
            raise ValueError('queue_timeout_ms must be >= 1')

        self.max_concurrent = max_concurrent
        self.queue_timeout_ms = queue_timeout_ms
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._rejected = 0

    async def acquire(self, trace_id: str) -> float:
        """Acquire one slot and return queue wait time in milliseconds."""
        started = perf_counter()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.queue_timeout_ms / 1000,
            )
        except TimeoutError as exc:
            self._rejected += 1
            logger.warning(
                '[%s] AI 请求并发已满: active=%d max=%d queue_timeout_ms=%d',
                trace_id,
                self._active,
                self.max_concurrent,
                self.queue_timeout_ms,
            )
            raise ConcurrencyLimitExceeded('AI request concurrency limit exceeded') from exc

        self._active += 1
        wait_ms = (perf_counter() - started) * 1000
        logger.info(
            '[%s] AI 请求获得并发槽: active=%d max=%d queue_wait_ms=%.1f',
            trace_id,
            self._active,
            self.max_concurrent,
            wait_ms,
        )
        return wait_ms

    def release(self, trace_id: str) -> None:
        if self._active <= 0:
            raise RuntimeError('concurrency limiter released without an active request')
        self._active -= 1
        self._semaphore.release()
        logger.info(
            '[%s] AI 请求释放并发槽: active=%d max=%d',
            trace_id,
            self._active,
            self.max_concurrent,
        )

    def snapshot(self) -> dict[str, int]:
        return {
            'maxConcurrent': self.max_concurrent,
            'active': self._active,
            'available': self.max_concurrent - self._active,
            'rejected': self._rejected,
            'queueTimeoutMs': self.queue_timeout_ms,
        }


ai_request_limiter = RequestConcurrencyLimiter(
    max_concurrent=AI_MAX_CONCURRENT_REQUESTS,
    queue_timeout_ms=AI_QUEUE_TIMEOUT_MS,
)
