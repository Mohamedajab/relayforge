from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or 500 <= status_code <= 599


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class RetryPolicy:
    def __init__(
        self,
        *,
        base_seconds: float,
        max_seconds: float,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self.jitter = jitter

    def delay(self, failed_attempt: int, retry_after: float | None = None) -> float:
        cap = min(self.max_seconds, self.base_seconds * 2 ** max(0, failed_attempt - 1))
        backoff = self.jitter(0.0, cap)
        if retry_after is not None:
            backoff = max(backoff, min(self.max_seconds, retry_after))
        return backoff
