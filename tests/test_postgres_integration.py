from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine

from relayforge.config import Settings
from relayforge.database import create_session_factory
from relayforge.schemas import EndpointCreate, EventCreate
from relayforge.security import TargetValidator
from relayforge.service import RelayService

pytestmark = pytest.mark.integration


def test_competing_workers_claim_delivery_once_on_postgres() -> None:
    database_url = os.getenv("RELAYFORGE_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("RELAYFORGE_TEST_POSTGRES_URL is not configured")

    engine = create_engine(database_url, pool_pre_ping=True)
    factory = create_session_factory(engine)
    settings = Settings(database_url=database_url, allow_private_targets=True)
    validator = TargetValidator(
        allow_private=True,
        resolver=lambda _host, _port: ["127.0.0.1"],
    )
    service = RelayService(factory, settings, target_validator=validator)
    endpoint, _ = service.create_endpoint(
        EndpointCreate(
            name="postgres-concurrency-proof",
            url="https://receiver.example/webhooks",
            secret="postgres-test-secret",
        )
    )
    event, _, _ = service.publish_event(
        EventCreate(
            source="postgres-integration",
            event_type="proof.created",
            idempotency_key=endpoint.id,
            payload={"test": "competing workers"},
        )
    )
    barrier = Barrier(2)

    def claim(owner: str) -> list[str]:
        barrier.wait(timeout=5)
        return service.claim_due(owner, limit=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))

    claimed = [delivery_id for result in results for delivery_id in result]
    assert len(claimed) == 1
    assert service.list_deliveries()[0].event_id == event.id
