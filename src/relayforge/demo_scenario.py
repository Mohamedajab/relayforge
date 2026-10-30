"""A bounded demonstration using the actual service, worker and example receiver."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from relayforge.config import Settings
from relayforge.database import create_database_engine, create_schema, create_session_factory
from relayforge.example_receiver import app, reset_demo_state
from relayforge.models import DeliveryState
from relayforge.schemas import EndpointCreate, EventCreate
from relayforge.service import RelayService
from relayforge.worker import DeliveryWorker


async def recovery_steps() -> AsyncIterator[dict[str, object]]:
    """Caller must serialize runs: the example receiver has process-local demo state."""
    started = time.monotonic()

    def step(stage: str, message: str, **values: object) -> dict[str, object]:
        return {
            "stage": stage,
            "message": message,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            **values,
        }

    os.environ["RELAYFORGE_RECEIVER_SECRET"] = "local-demo-secret-only"
    os.environ["RELAYFORGE_DEMO_FAIL_FIRST"] = "2"
    reset_demo_state()
    with TemporaryDirectory() as directory:
        settings = Settings(
            database_url=f"sqlite:///{Path(directory).as_posix()}/demo.db",
            allow_private_targets=True,
            allow_insecure_http=True,
            lease_seconds=1,
            base_retry_seconds=1,
            max_retry_seconds=1,
            max_attempts=3,
        )
        engine = create_database_engine(settings.database_url)
        create_schema(engine)
        factory = create_session_factory(engine)
        service = RelayService(factory, settings)
        try:
            service.create_endpoint(
                EndpointCreate(
                    name="demo",
                    url="http://127.0.0.1/webhooks/flaky",
                    secret="local-demo-secret-only",
                )
            )
            event = EventCreate(
                source="demo",
                event_type="order.created",
                idempotency_key="order-1",
                payload={"order": 1},
            )
            service.publish_event(event)
            if len(service.claim_due("abandoned-worker")) != 1:
                raise RuntimeError("Demo worker did not claim exactly one delivery")
            yield step("claimed", "Worker A claimed the delivery, then left it unfinished.")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                worker = DeliveryWorker(factory, settings, owner="replacement", client=client)
                if await worker.run_once() != 0:
                    raise RuntimeError("Live lease was not respected")
                yield step("leased", "Worker B must wait until the existing lease expires.")
                deadline = time.monotonic() + 12
                last_attempt = 0
                while time.monotonic() < deadline:
                    await worker.run_once()
                    delivery = service.list_deliveries()[0]
                    if delivery.attempt_count != last_attempt:
                        yield step(
                            "attempt",
                            f"Attempt {delivery.attempt_count}: HTTP "
                            f"{delivery.last_status_code}, state={delivery.state.value}",
                            attempt=delivery.attempt_count,
                            status=delivery.last_status_code,
                        )
                        last_attempt = delivery.attempt_count
                    if delivery.state == DeliveryState.SUCCEEDED:
                        break
                    await asyncio.sleep(0.1)
                if delivery.state != DeliveryState.SUCCEEDED or delivery.attempt_count != 3:
                    raise RuntimeError("Delivery did not recover in three attempts")
                received = (await client.get("http://127.0.0.1/received")).json()
                service.publish_event(event)
                if len(received) != 1 or len(service.list_deliveries()) != 1:
                    raise RuntimeError("Receiver or producer deduplication failed")
                yield step(
                    "verified",
                    "One receiver record. Replaying the event created no second delivery.",
                    attempts=3,
                    received=1,
                    deliveries=1,
                )
        finally:
            engine.dispose()
