import asyncio

import pytest

from app.core.concurrency import ConcurrencyLimitExceeded, RequestConcurrencyLimiter


def test_limiter_rejects_after_queue_timeout_and_recovers():
    async def scenario():
        limiter = RequestConcurrencyLimiter(max_concurrent=1, queue_timeout_ms=10)

        await limiter.acquire('first')
        assert limiter.snapshot() == {
            'maxConcurrent': 1,
            'active': 1,
            'available': 0,
            'rejected': 0,
            'queueTimeoutMs': 10,
        }

        with pytest.raises(ConcurrencyLimitExceeded):
            await limiter.acquire('second')

        assert limiter.snapshot()['rejected'] == 1
        limiter.release('first')

        await limiter.acquire('third')
        assert limiter.snapshot()['active'] == 1
        limiter.release('third')
        assert limiter.snapshot()['available'] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ('max_concurrent', 'queue_timeout_ms'),
    [(0, 10), (1, 0)],
)
def test_limiter_rejects_invalid_settings(max_concurrent, queue_timeout_ms):
    with pytest.raises(ValueError):
        RequestConcurrencyLimiter(max_concurrent, queue_timeout_ms)
