from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relayforge.clock import utc_now
from relayforge.models import Delivery, DeliveryState
from relayforge.schemas import EndpointUpdate, EventCreate
from relayforge.service import IdempotencyConflict, InvalidStateError, RelayService


def event(key: str = "evt-1", amount: int = 25) -> EventCreate:
    return EventCreate(
        source="checkout",
        event_type="order.created",
        idempotency_key=key,
        payload={"order_id": "ord-7", "amount": amount},
    )


def test_publish_is_idempotent_and_filters_subscriptions(
    service: RelayService, make_endpoint: object
) -> None:
    make_endpoint(name="orders", event_types=["order.created"])
    make_endpoint(name="invoices", event_types=["invoice.created"])

    created, was_created, count = service.publish_event(event())
    duplicate, duplicate_created, duplicate_count = service.publish_event(event())

    assert was_created is True
    assert count == 1
    assert duplicate_created is False
    assert duplicate_count == 1
    assert duplicate.id == created.id
    assert len(service.list_deliveries()) == 1


def test_idempotency_key_cannot_change_payload(
    service: RelayService, make_endpoint: object
) -> None:
    make_endpoint()
    service.publish_event(event(amount=10))

    with pytest.raises(IdempotencyConflict):
        service.publish_event(event(amount=11))


def test_endpoint_update_and_event_read(service: RelayService, make_endpoint: object) -> None:
    endpoint = make_endpoint()
    updated = service.update_endpoint(
        endpoint.id, EndpointUpdate(name="renamed", event_types=["order.created"])
    )
    saved, _, _ = service.publish_event(event())

    assert updated.name == "renamed"
    assert updated.event_types == ["order.created"]
    assert service.get_event(saved.id).payload["order_id"] == "ord-7"
    assert service.list_endpoints()[0].id == endpoint.id


def test_claim_lease_prevents_double_claim_then_recovers(
    service: RelayService, make_endpoint: object
) -> None:
    make_endpoint()
    service.publish_event(event())
    now = utc_now()

    first = service.claim_due("worker-a", now=now)
    second = service.claim_due("worker-b", now=now + timedelta(seconds=5))
    recovered = service.claim_due("worker-b", now=now + timedelta(seconds=31))

    assert len(first) == 1
    assert second == []
    assert recovered == first


def test_only_dead_deliveries_can_be_replayed(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
) -> None:
    make_endpoint()
    service.publish_event(event())
    delivery = service.list_deliveries()[0]
    with pytest.raises(InvalidStateError):
        service.replay_delivery(delivery.id)

    with factory() as session:
        stored = session.scalar(select(Delivery).where(Delivery.id == delivery.id))
        assert stored is not None
        stored.state = DeliveryState.DEAD
        session.commit()

    replayed = service.replay_delivery(delivery.id)
    assert replayed.state == DeliveryState.PENDING
    assert replayed.last_error is None
