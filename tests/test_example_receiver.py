from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from relayforge.example_receiver import app, reset_demo_state
from relayforge.security import canonical_json, sign_payload


@pytest.fixture(autouse=True)
def receiver_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_demo_state()
    monkeypatch.setenv("RELAYFORGE_RECEIVER_SECRET", "receiver-test-secret")
    monkeypatch.setenv("RELAYFORGE_DEMO_FAIL_FIRST", "2")


def signed_headers(
    delivery_id: str, body: bytes, *, timestamp: int | None = None
) -> dict[str, str]:
    timestamp = timestamp or int(time.time())
    return {
        "Content-Type": "application/json",
        "X-RelayForge-Delivery-Id": delivery_id,
        "X-RelayForge-Timestamp": str(timestamp),
        "X-RelayForge-Signature": sign_payload("receiver-test-secret", timestamp, body),
    }


def test_receiver_verifies_and_deduplicates() -> None:
    client = TestClient(app)
    body = canonical_json({"id": "event-1", "type": "order.created", "data": {}})
    headers = signed_headers("delivery-1", body)

    first = client.post("/webhooks", content=body, headers=headers)
    duplicate = client.post("/webhooks", content=body, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 200
    received = client.get("/received").json()
    assert len(received) == 1
    assert received[0]["event_type"] == "order.created"


def test_flaky_receiver_recovers_on_third_attempt() -> None:
    client = TestClient(app)
    body = canonical_json({"id": "event-2", "type": "invoice.paid", "data": {}})
    headers = signed_headers("delivery-2", body)

    statuses = [
        client.post("/webhooks/flaky", content=body, headers=headers).status_code for _ in range(3)
    ]

    assert statuses == [503, 503, 202]
    assert len(client.get("/received").json()) == 1


def test_receiver_rejects_bad_signature_and_stale_timestamp() -> None:
    client = TestClient(app)
    body = canonical_json({"id": "event-3", "type": "test", "data": {}})
    headers = signed_headers("delivery-3", body)
    headers["X-RelayForge-Signature"] = "v1=bad"
    assert client.post("/webhooks", content=body, headers=headers).status_code == 401

    stale = int(time.time()) - 1_000
    stale_headers = signed_headers("delivery-3", body, timestamp=stale)
    assert client.post("/webhooks", content=body, headers=stale_headers).status_code == 401
