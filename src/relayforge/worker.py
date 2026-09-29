from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from relayforge.clock import utc_now
from relayforge.config import Settings
from relayforge.models import AuditEntry, Delivery, DeliveryAttempt, DeliveryState
from relayforge.retry import RetryPolicy, is_retryable_status, parse_retry_after
from relayforge.security import TargetValidator, UnsafeTargetError, canonical_json, sign_payload
from relayforge.service import RelayService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    delivery_id: str
    event_id: str
    endpoint_id: str
    endpoint_url: str
    endpoint_secret: str
    event_type: str
    event_created_at: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status_code: int | None
    error: str | None
    response_body: str | None
    retryable: bool
    retry_after: float | None = None

    @property
    def succeeded(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code <= 299


class DeliveryWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        owner: str | None = None,
        client: httpx.AsyncClient | None = None,
        target_validator: TargetValidator | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.owner = owner or f"{socket.gethostname()}:{id(self)}"
        self.client = client
        self.target_validator = target_validator or TargetValidator(
            allow_private=settings.allow_private_targets,
            allow_insecure_http=settings.allow_insecure_http,
        )
        self.retry_policy = retry_policy or RetryPolicy(
            base_seconds=settings.base_retry_seconds,
            max_seconds=settings.max_retry_seconds,
        )
        self.service = RelayService(
            session_factory, settings, target_validator=self.target_validator
        )

    async def run_once(self, *, batch_size: int = 25, concurrency: int = 8) -> int:
        delivery_ids = self.service.claim_due(self.owner, limit=batch_size)
        if not delivery_ids:
            return 0

        semaphore = asyncio.Semaphore(concurrency)
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
        )

        async def bounded(delivery_id: str) -> None:
            async with semaphore:
                await self.process(delivery_id, client)

        try:
            await asyncio.gather(*(bounded(delivery_id) for delivery_id in delivery_ids))
        finally:
            if owns_client:
                await client.aclose()
        return len(delivery_ids)

    async def run_forever(
        self, *, poll_seconds: float = 1.0, batch_size: int = 25, concurrency: int = 8
    ) -> None:
        logger.info("worker_started", extra={"worker": self.owner})
        while True:
            processed = await self.run_once(batch_size=batch_size, concurrency=concurrency)
            if not processed:
                await asyncio.sleep(poll_seconds)

    async def process(self, delivery_id: str, client: httpx.AsyncClient) -> bool:
        job = self._load_job(delivery_id)
        if job is None:
            return False

        envelope = {
            "id": job.event_id,
            "type": job.event_type,
            "created_at": job.event_created_at,
            "data": job.payload,
        }
        body = canonical_json(envelope)
        started_at = utc_now()
        started_clock = time.perf_counter()
        timestamp = int(started_at.timestamp())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "RelayForge/0.1",
            "X-RelayForge-Event-Id": job.event_id,
            "X-RelayForge-Delivery-Id": job.delivery_id,
            "X-RelayForge-Attempt": str(job.attempt_count + 1),
            "X-RelayForge-Timestamp": str(timestamp),
            "X-RelayForge-Signature": sign_payload(job.endpoint_secret, timestamp, body),
        }

        outcome: DeliveryOutcome
        try:
            self.target_validator.validate(job.endpoint_url)
            response = await client.post(job.endpoint_url, content=body, headers=headers)
            response_body = response.text[: self.settings.response_body_limit]
            outcome = DeliveryOutcome(
                status_code=response.status_code,
                error=None if response.is_success else f"HTTP {response.status_code}",
                response_body=response_body,
                retryable=is_retryable_status(response.status_code),
                retry_after=parse_retry_after(response.headers.get("Retry-After"), started_at),
            )
        except UnsafeTargetError as exc:
            outcome = DeliveryOutcome(
                status_code=None,
                error=f"unsafe target: {exc}",
                response_body=None,
                retryable=False,
            )
        except httpx.TransportError as exc:
            outcome = DeliveryOutcome(
                status_code=None,
                error=f"{type(exc).__name__}: {exc}",
                response_body=None,
                retryable=True,
            )

        finished_at = utc_now()
        duration_ms = max(0, round((time.perf_counter() - started_clock) * 1_000))
        return self._finalize(job, outcome, started_at, finished_at, duration_ms)

    def _load_job(self, delivery_id: str) -> DeliveryJob | None:
        with self.session_factory() as session:
            delivery = session.scalar(
                select(Delivery)
                .options(joinedload(Delivery.event), joinedload(Delivery.endpoint))
                .where(Delivery.id == delivery_id)
            )
            if (
                delivery is None
                or delivery.state != DeliveryState.IN_FLIGHT
                or delivery.lease_owner != self.owner
            ):
                return None
            return DeliveryJob(
                delivery_id=delivery.id,
                event_id=delivery.event.id,
                endpoint_id=delivery.endpoint.id,
                endpoint_url=delivery.endpoint.url,
                endpoint_secret=delivery.endpoint.secret,
                event_type=delivery.event.event_type,
                event_created_at=delivery.event.created_at.isoformat(),
                payload=delivery.event.payload,
                attempt_count=delivery.attempt_count,
            )

    def _finalize(
        self,
        job: DeliveryJob,
        outcome: DeliveryOutcome,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: int,
    ) -> bool:
        with self.session_factory() as session:
            delivery = session.get(Delivery, job.delivery_id)
            if (
                delivery is None
                or delivery.state != DeliveryState.IN_FLIGHT
                or delivery.lease_owner != self.owner
                or delivery.attempt_count != job.attempt_count
            ):
                return False

            attempt_number = job.attempt_count + 1
            session.add(
                DeliveryAttempt(
                    delivery_id=delivery.id,
                    attempt_number=attempt_number,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status_code=outcome.status_code,
                    error=outcome.error,
                    response_body=outcome.response_body,
                )
            )
            delivery.attempt_count = attempt_number
            delivery.last_status_code = outcome.status_code
            delivery.last_error = outcome.error
            delivery.last_response_body = outcome.response_body
            delivery.lease_owner = None
            delivery.lease_expires_at = None

            if outcome.succeeded:
                delivery.state = DeliveryState.SUCCEEDED
                delivery.delivered_at = finished_at
                action = "delivery.succeeded"
                details: dict[str, object] = {
                    "attempt": attempt_number,
                    "status_code": outcome.status_code or 0,
                    "duration_ms": duration_ms,
                }
            elif outcome.retryable and attempt_number < self.settings.max_attempts:
                delay = self.retry_policy.delay(attempt_number, outcome.retry_after)
                delivery.state = DeliveryState.RETRYING
                delivery.next_attempt_at = utc_now() + timedelta(seconds=delay)
                action = "delivery.retry_scheduled"
                details = {"attempt": attempt_number, "delay_seconds": round(delay, 3)}
            else:
                delivery.state = DeliveryState.DEAD
                action = "delivery.dead_lettered"
                details = {"attempt": attempt_number, "error": outcome.error or "delivery failed"}

            session.add(
                AuditEntry(
                    action=action,
                    entity_type="delivery",
                    entity_id=delivery.id,
                    details=details,
                )
            )
            session.commit()
            return True
