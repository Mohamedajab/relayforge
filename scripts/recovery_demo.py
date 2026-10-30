"""Run the real worker and example receiver in a disposable, in-process demo."""

import argparse
import asyncio
import json
import os
import time
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


async def demonstrate(recording: Path) -> None:
    started = time.monotonic()
    frames: list[list[object]] = []

    def report(message: str) -> None:
        print(message, flush=True)
        frames.append([round(time.monotonic() - started, 3), "o", message + "\r\n"])

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
            assert len(service.claim_due("abandoned-worker")) == 1
            report("A worker claimed the delivery and stopped before sending it.")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                worker = DeliveryWorker(factory, settings, owner="replacement", client=client)
                assert await worker.run_once() == 0
                report("Replacement cannot claim the delivery while its lease is live.")
                deadline = time.monotonic() + 20
                last_attempt = 0
                while time.monotonic() < deadline:
                    await worker.run_once()
                    delivery = service.list_deliveries()[0]
                    if delivery.attempt_count != last_attempt:
                        report(
                            f"Attempt {delivery.attempt_count}: HTTP {delivery.last_status_code}, "
                            f"state={delivery.state.value}"
                        )
                        last_attempt = delivery.attempt_count
                    if delivery.state == DeliveryState.SUCCEEDED:
                        break
                    await asyncio.sleep(0.1)
                assert delivery.state == DeliveryState.SUCCEEDED
                assert delivery.attempt_count == 3
                assert len((await client.get("http://127.0.0.1/received")).json()) == 1
                service.publish_event(event)
                assert len(service.list_deliveries()) == 1
                report(
                    "Receiver recorded one delivery. Producer replay created no second delivery."
                )
        finally:
            engine.dispose()
    recording.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "version": 2,
        "width": 100,
        "height": 14,
        "title": "RelayForge local recovery demonstration",
    }
    recording.write_text("\n".join(json.dumps(row) for row in [header, *frames]) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, default=Path("docs/evidence/recovery.cast"))
    asyncio.run(demonstrate(parser.parse_args().recording))
