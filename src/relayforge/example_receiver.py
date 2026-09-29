from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response, status

from relayforge.security import verify_signature

app = FastAPI(
    title="RelayForge example receiver",
    description="Verifies signatures, rejects stale requests and deduplicates deliveries.",
)

_lock = Lock()
_seen: set[str] = set()
_attempts: dict[str, int] = defaultdict(int)
_received: list[dict[str, Any]] = []


def _secret() -> str:
    value = os.getenv("RELAYFORGE_RECEIVER_SECRET")
    if not value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RELAYFORGE_RECEIVER_SECRET is not configured",
        )
    return value


async def _verify(
    request: Request,
    delivery_id: str,
    timestamp_header: str,
    signature: str,
) -> tuple[bytes, dict[str, Any]]:
    try:
        timestamp = int(timestamp_header)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid timestamp") from exc
    if abs(time.time() - timestamp) > 300:
        raise HTTPException(status_code=401, detail="stale timestamp")
    body = await request.body()
    if not verify_signature(_secret(), timestamp, body, signature):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="missing delivery ID")
    return body, payload


def _record(delivery_id: str, payload: dict[str, Any]) -> bool:
    with _lock:
        if delivery_id in _seen:
            return False
        _seen.add(delivery_id)
        _received.append(
            {
                "delivery_id": delivery_id,
                "event_id": payload.get("id"),
                "event_type": payload.get("type"),
                "received_at": datetime.now(UTC).isoformat(),
            }
        )
        return True


@app.post("/webhooks")
async def receive(
    request: Request,
    x_relayforge_delivery_id: str = Header(alias="X-RelayForge-Delivery-Id"),
    x_relayforge_timestamp: str = Header(alias="X-RelayForge-Timestamp"),
    x_relayforge_signature: str = Header(alias="X-RelayForge-Signature"),
) -> Response:
    _, payload = await _verify(
        request,
        x_relayforge_delivery_id,
        x_relayforge_timestamp,
        x_relayforge_signature,
    )
    created = _record(x_relayforge_delivery_id, payload)
    return Response(status_code=202 if created else 200)


@app.post("/webhooks/flaky")
async def receive_after_transient_failures(
    request: Request,
    x_relayforge_delivery_id: str = Header(alias="X-RelayForge-Delivery-Id"),
    x_relayforge_timestamp: str = Header(alias="X-RelayForge-Timestamp"),
    x_relayforge_signature: str = Header(alias="X-RelayForge-Signature"),
) -> Response:
    _, payload = await _verify(
        request,
        x_relayforge_delivery_id,
        x_relayforge_timestamp,
        x_relayforge_signature,
    )
    fail_first = max(0, int(os.getenv("RELAYFORGE_DEMO_FAIL_FIRST", "2")))
    with _lock:
        _attempts[x_relayforge_delivery_id] += 1
        attempt = _attempts[x_relayforge_delivery_id]
    if attempt <= fail_first:
        return Response(status_code=503, headers={"Retry-After": "1"})
    created = _record(x_relayforge_delivery_id, payload)
    return Response(status_code=202 if created else 200)


@app.get("/received")
def received() -> list[dict[str, Any]]:
    with _lock:
        return list(_received)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


def reset_demo_state() -> None:
    with _lock:
        _seen.clear()
        _attempts.clear()
        _received.clear()


def run() -> None:
    host = os.getenv("RELAYFORGE_RECEIVER_HOST", "127.0.0.1")
    port = int(os.getenv("RELAYFORGE_RECEIVER_PORT", "9000"))
    uvicorn.run("relayforge.example_receiver:app", host=host, port=port)
