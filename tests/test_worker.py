from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from relayforge.clock import utc_now
from relayforge.config import Settings
from relayforge.models import DeliveryState
from relayforge.retry import RetryPolicy
from relayforge.schemas import EventCreate
from relayforge.security import TargetValidator, verify_signature
from relayforge.service import RelayService
from relayforge.worker import DeliveryWorker


def order_event(key: str) -> EventCreate:
    return EventCreate(
        source="checkout",
        event_type="order.created",
        idempotency_key=key,
        payload={"order_id": key},
    )


@pytest.mark.asyncio
async def test_worker_retries_then_succeeds_with_valid_signature(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    secret = "worker-secret-at-least-16"
    make_endpoint(secret=secret)
    service.publish_event(order_event("worker-1"))
    statuses = iter([503, 204])
    seen_signatures: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamp = int(request.headers["X-RelayForge-Timestamp"])
        seen_signatures.append(
            verify_signature(
                secret,
                timestamp,
                request.content,
                request.headers["X-RelayForge-Signature"],
            )
        )
        return httpx.Response(next(statuses), headers={"Retry-After": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="worker-test",
            client=client,
            target_validator=validator,
            retry_policy=RetryPolicy(
                base_seconds=1, max_seconds=10, jitter=lambda _low, high: high
            ),
        )
        assert await worker.run_once() == 1
        retrying = service.list_deliveries()[0]
        assert retrying.state == DeliveryState.RETRYING
        assert retrying.attempt_count == 1
        assert retrying.last_status_code == 503

        with factory() as session:
            stored = session.get(type(retrying), retrying.id)
            assert stored is not None
            stored.next_attempt_at = utc_now() - timedelta(seconds=1)
            session.commit()

        assert await worker.run_once() == 1

    delivered = service.get_delivery(retrying.id)
    assert delivered.state == DeliveryState.SUCCEEDED
    assert delivered.attempt_count == 2
    assert delivered.last_status_code == 204
    assert len(delivered.attempts) == 2
    assert seen_signatures == [True, True]


@pytest.mark.asyncio
async def test_non_retryable_response_goes_to_dead_letter(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    make_endpoint()
    service.publish_event(order_event("worker-400"))

    transport = httpx.MockTransport(lambda _request: httpx.Response(400, text="invalid"))
    async with httpx.AsyncClient(transport=transport) as client:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="worker-dead",
            client=client,
            target_validator=validator,
        )
        assert await worker.run_once() == 1

    delivery = service.list_deliveries()[0]
    assert delivery.state == DeliveryState.DEAD
    assert delivery.attempt_count == 1
    assert delivery.last_error == "HTTP 400"


@pytest.mark.asyncio
async def test_stale_worker_cannot_finalize_reclaimed_delivery(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    make_endpoint()
    service.publish_event(order_event("stale-1"))
    delivery_id = service.claim_due("other-worker")[0]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    try:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="stale-worker",
            client=client,
            target_validator=validator,
        )
        assert await worker.process(delivery_id, client) is False
    finally:
        await client.aclose()
