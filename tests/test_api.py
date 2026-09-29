from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from relayforge.api import create_app
from relayforge.config import Settings
from relayforge.security import TargetValidator
from relayforge.service import RelayService


def test_api_auth_endpoint_creation_and_idempotency(
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    service = RelayService(factory, settings, target_validator=validator)
    client = TestClient(
        create_app(
            settings,
            session_factory=factory,
            service=service,
            create_tables=False,
        )
    )
    headers = {"X-API-Key": settings.api_key}

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/v1/endpoints").status_code == 401

    endpoint_response = client.post(
        "/v1/endpoints",
        headers=headers,
        json={
            "name": "orders",
            "url": "http://receiver.test/webhooks",
            "event_types": ["order.created"],
        },
    )
    assert endpoint_response.status_code == 201
    assert len(endpoint_response.json()["secret"]) >= 32
    assert "secret" not in client.get("/v1/endpoints", headers=headers).json()[0]

    payload = {
        "source": "checkout",
        "event_type": "order.created",
        "idempotency_key": "evt-api-1",
        "payload": {"order_id": "ord-1"},
    }
    first = client.post("/v1/events", headers=headers, json=payload)
    duplicate = client.post("/v1/events", headers=headers, json=payload)
    conflict_payload = payload | {"payload": {"order_id": "different"}}
    conflict = client.post("/v1/events", headers=headers, json=conflict_payload)

    assert first.status_code == 202
    assert first.json()["delivery_count"] == 1
    assert duplicate.json()["duplicate"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(client.get("/v1/deliveries", headers=headers).json()) == 1


def test_api_validation_and_not_found_errors(
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    service = RelayService(factory, settings, target_validator=validator)
    client = TestClient(
        create_app(settings, session_factory=factory, service=service, create_tables=False)
    )
    headers = {"X-API-Key": settings.api_key}

    missing = client.get("/v1/events/missing", headers=headers)
    invalid = client.post(
        "/v1/events",
        headers=headers,
        json={
            "source": "bad source",
            "event_type": "event",
            "idempotency_key": "x",
            "payload": {},
        },
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422


def test_metrics_expose_queue_state(
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    app = create_app(
        settings,
        session_factory=factory,
        service=RelayService(factory, settings, target_validator=validator),
        create_tables=False,
    )
    body = TestClient(app).get("/metrics").text
    assert 'relayforge_deliveries{state="pending"} 0' in body
    assert "relayforge_delivery_attempts_total 0" in body
