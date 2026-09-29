from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from relayforge.retry import RetryPolicy, is_retryable_status, parse_retry_after
from relayforge.security import (
    TargetValidator,
    UnsafeTargetError,
    canonical_json,
    sign_payload,
    verify_signature,
)


def test_canonical_json_and_signature_are_stable() -> None:
    first = canonical_json({"b": 2, "a": "value"})
    second = canonical_json({"a": "value", "b": 2})

    assert first == second == b'{"a":"value","b":2}'
    signature = sign_payload("secret", 1_700_000_000, first)
    assert verify_signature("secret", 1_700_000_000, second, signature)
    assert not verify_signature("different", 1_700_000_000, second, signature)
    assert not verify_signature("secret", 1_700_000_001, second, signature)


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503, 599])
def test_retryable_statuses(status_code: int) -> None:
    assert is_retryable_status(status_code)


@pytest.mark.parametrize("status_code", [200, 301, 400, 401, 404, 600])
def test_non_retryable_statuses(status_code: int) -> None:
    assert not is_retryable_status(status_code)


def test_retry_policy_uses_exponential_cap_and_retry_after() -> None:
    policy = RetryPolicy(base_seconds=2, max_seconds=10, jitter=lambda _low, high: high)
    assert policy.delay(1) == 2
    assert policy.delay(3) == 8
    assert policy.delay(10) == 10
    assert policy.delay(1, retry_after=7) == 7
    assert policy.delay(1, retry_after=99) == 10


def test_parse_retry_after_supports_seconds_and_http_dates() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_retry_after("12", now) == 12
    future = now + timedelta(seconds=30)
    assert parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), now) == 30
    assert parse_retry_after("not-a-date", now) is None
    assert parse_retry_after(None, now) is None


def test_target_validator_rejects_ssrf_and_unsafe_schemes() -> None:
    validator = TargetValidator(resolver=lambda _host, _port: ["127.0.0.1"])
    with pytest.raises(UnsafeTargetError, match="plain HTTP"):
        validator.validate("http://example.com/hooks")
    with pytest.raises(UnsafeTargetError, match="private"):
        validator.validate("https://example.com/hooks")
    with pytest.raises(UnsafeTargetError, match="credentials"):
        validator.validate("https://user:pass@example.com/hooks")
    with pytest.raises(UnsafeTargetError, match="fragments"):
        validator.validate("https://example.com/hooks#secret")
    with pytest.raises(UnsafeTargetError, match="http or https"):
        validator.validate("file:///etc/passwd")


def test_target_validator_accepts_public_https() -> None:
    validator = TargetValidator(resolver=lambda _host, _port: ["93.184.216.34"])
    validator.validate("https://example.com/hooks")
