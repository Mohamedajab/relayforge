from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from relayforge.clock import utc_now
from relayforge.config import Settings
from relayforge.models import AuditEntry, Delivery, DeliveryState, Endpoint, Event
from relayforge.schemas import EndpointCreate, EndpointUpdate, EventCreate
from relayforge.security import TargetValidator, sha256_payload


class IdempotencyConflict(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class InvalidStateError(ValueError):
    pass


def _eligible(now: datetime) -> ColumnElement[bool]:
    return or_(
        and_(
            Delivery.state.in_([DeliveryState.PENDING, DeliveryState.RETRYING]),
            Delivery.next_attempt_at <= now,
        ),
        and_(
            Delivery.state == DeliveryState.IN_FLIGHT,
            Delivery.lease_expires_at.is_not(None),
            Delivery.lease_expires_at <= now,
        ),
    )


class RelayService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        target_validator: TargetValidator | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.target_validator = target_validator or TargetValidator(
            allow_private=settings.allow_private_targets,
            allow_insecure_http=settings.allow_insecure_http,
        )

    def create_endpoint(self, data: EndpointCreate) -> tuple[Endpoint, str]:
        url = str(data.url)
        self.target_validator.validate(url)
        secret = data.secret or secrets.token_urlsafe(32)
        with self.session_factory() as session:
            endpoint = Endpoint(
                name=data.name,
                url=url,
                event_types=data.event_types,
                secret=secret,
            )
            session.add(endpoint)
            session.flush()
            self._audit(session, "endpoint.created", "endpoint", endpoint.id, {"name": data.name})
            session.commit()
            return endpoint, secret

    def list_endpoints(self) -> list[Endpoint]:
        with self.session_factory() as session:
            return list(session.scalars(select(Endpoint).order_by(Endpoint.created_at.desc())))

    def update_endpoint(self, endpoint_id: str, data: EndpointUpdate) -> Endpoint:
        with self.session_factory() as session:
            endpoint = session.get(Endpoint, endpoint_id)
            if endpoint is None:
                raise NotFoundError("endpoint not found")
            changes = data.model_dump(exclude_unset=True)
            if "url" in changes:
                changes["url"] = str(changes["url"])
                self.target_validator.validate(changes["url"])
            for key, value in changes.items():
                setattr(endpoint, key, value)
            self._audit(
                session, "endpoint.updated", "endpoint", endpoint.id, {"fields": sorted(changes)}
            )
            session.commit()
            return endpoint

    def publish_event(self, data: EventCreate) -> tuple[Event, bool, int]:
        digest = sha256_payload(data.payload)
        with self.session_factory() as session:
            existing = session.scalar(
                select(Event).where(
                    Event.source == data.source, Event.idempotency_key == data.idempotency_key
                )
            )
            if existing is not None:
                self._validate_duplicate(existing, data, digest)
                return existing, False, len(existing.deliveries)

            event = Event(
                source=data.source,
                event_type=data.event_type,
                idempotency_key=data.idempotency_key,
                payload=data.payload,
                payload_sha256=digest,
            )
            session.add(event)
            session.flush()
            endpoints = session.scalars(select(Endpoint).where(Endpoint.is_active.is_(True))).all()
            deliveries = [
                Delivery(event_id=event.id, endpoint_id=endpoint.id)
                for endpoint in endpoints
                if not endpoint.event_types or data.event_type in endpoint.event_types
            ]
            session.add_all(deliveries)
            self._audit(
                session,
                "event.published",
                "event",
                event.id,
                {"event_type": event.event_type, "delivery_count": len(deliveries)},
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(Event).where(
                        Event.source == data.source,
                        Event.idempotency_key == data.idempotency_key,
                    )
                )
                if existing is None:
                    raise
                self._validate_duplicate(existing, data, digest)
                return existing, False, len(existing.deliveries)
            return event, True, len(deliveries)

    def get_event(self, event_id: str) -> Event:
        with self.session_factory() as session:
            event = session.get(Event, event_id)
            if event is None:
                raise NotFoundError("event not found")
            return event

    def get_delivery(self, delivery_id: str) -> Delivery:
        with self.session_factory() as session:
            delivery = session.scalar(
                select(Delivery)
                .options(selectinload(Delivery.attempts))
                .where(Delivery.id == delivery_id)
            )
            if delivery is None:
                raise NotFoundError("delivery not found")
            return delivery

    def list_deliveries(
        self, state: DeliveryState | None = None, *, limit: int = 100
    ) -> list[Delivery]:
        statement = select(Delivery).options(selectinload(Delivery.attempts))
        if state is not None:
            statement = statement.where(Delivery.state == state)
        statement = statement.order_by(Delivery.created_at.desc()).limit(min(limit, 500))
        with self.session_factory() as session:
            return list(session.scalars(statement))

    def replay_delivery(self, delivery_id: str) -> Delivery:
        with self.session_factory() as session:
            delivery = session.get(Delivery, delivery_id)
            if delivery is None:
                raise NotFoundError("delivery not found")
            if delivery.state != DeliveryState.DEAD:
                raise InvalidStateError("only dead-lettered deliveries can be replayed")
            delivery.state = DeliveryState.PENDING
            delivery.next_attempt_at = utc_now()
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            delivery.last_error = None
            self._audit(session, "delivery.replayed", "delivery", delivery.id, {})
            session.commit()
            return delivery

    def claim_due(self, owner: str, *, limit: int = 25, now: datetime | None = None) -> list[str]:
        now = now or utc_now()
        expires = now + timedelta(seconds=self.settings.lease_seconds)
        claimed: list[str] = []
        with self.session_factory() as session:
            candidate_ids = session.scalars(
                select(Delivery.id)
                .where(_eligible(now))
                .order_by(Delivery.next_attempt_at, Delivery.created_at)
                .limit(limit * 3)
            ).all()
            for delivery_id in candidate_ids:
                result = session.execute(
                    update(Delivery)
                    .where(Delivery.id == delivery_id, _eligible(now))
                    .values(
                        state=DeliveryState.IN_FLIGHT,
                        lease_owner=owner,
                        lease_expires_at=expires,
                        updated_at=now,
                    )
                )
                if getattr(result, "rowcount", 0) == 1:
                    claimed.append(delivery_id)
                    self._audit(
                        session,
                        "delivery.claimed",
                        "delivery",
                        delivery_id,
                        {"worker": owner, "lease_expires_at": expires.isoformat()},
                    )
                if len(claimed) >= limit:
                    break
            session.commit()
        return claimed

    @staticmethod
    def _validate_duplicate(existing: Event, data: EventCreate, digest: str) -> None:
        if existing.event_type != data.event_type or existing.payload_sha256 != digest:
            raise IdempotencyConflict(
                "idempotency key was already used with a different event type or payload"
            )

    @staticmethod
    def _audit(
        session: Session,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, object],
    ) -> None:
        session.add(
            AuditEntry(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
        )
