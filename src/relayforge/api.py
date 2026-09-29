from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from relayforge.config import Settings
from relayforge.database import create_database_engine, create_schema, create_session_factory
from relayforge.metrics import render_metrics
from relayforge.models import DeliveryState
from relayforge.schemas import (
    DeliveryRead,
    EndpointCreate,
    EndpointCreated,
    EndpointRead,
    EndpointUpdate,
    ErrorResponse,
    EventAccepted,
    EventCreate,
    EventRead,
)
from relayforge.security import UnsafeTargetError
from relayforge.service import (
    IdempotencyConflict,
    InvalidStateError,
    NotFoundError,
    RelayService,
)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    service: RelayService | None = None,
    create_tables: bool = False,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if session_factory is None:
        engine = create_database_engine(settings.database_url)
        if create_tables:
            create_schema(engine)
        session_factory = create_session_factory(engine)
    service = service or RelayService(session_factory, settings)

    app = FastAPI(
        title="RelayForge",
        version="0.1.0",
        description="Durable webhook delivery with retries, signatures and dead-letter recovery.",
        responses={401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.service = service

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not secrets.compare_digest(x_api_key, settings.api_key):
            raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "invalid API key")

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = str(
            max(0, round((time.perf_counter() - started) * 1_000))
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and {"code", "message"} <= exc.detail.keys():
            detail = exc.detail
        else:
            detail = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    def ready() -> dict[str, str]:
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:
            raise _error(503, "database_unavailable", "database readiness check failed") from exc
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse, tags=["operations"])
    def metrics() -> str:
        return render_metrics(session_factory)

    secured = [Depends(require_api_key)]

    @app.post(
        "/v1/endpoints",
        response_model=EndpointCreated,
        status_code=status.HTTP_201_CREATED,
        dependencies=secured,
        tags=["endpoints"],
    )
    def create_endpoint(data: EndpointCreate) -> EndpointCreated:
        try:
            endpoint, secret = service.create_endpoint(data)
        except UnsafeTargetError as exc:
            raise _error(422, "unsafe_target", str(exc)) from exc
        endpoint_data = EndpointRead.model_validate(endpoint).model_dump()
        return EndpointCreated(**endpoint_data, secret=secret)

    @app.get(
        "/v1/endpoints",
        response_model=list[EndpointRead],
        dependencies=secured,
        tags=["endpoints"],
    )
    def list_endpoints() -> list[EndpointRead]:
        return [EndpointRead.model_validate(item) for item in service.list_endpoints()]

    @app.patch(
        "/v1/endpoints/{endpoint_id}",
        response_model=EndpointRead,
        dependencies=secured,
        tags=["endpoints"],
    )
    def update_endpoint(endpoint_id: str, data: EndpointUpdate) -> EndpointRead:
        try:
            return EndpointRead.model_validate(service.update_endpoint(endpoint_id, data))
        except NotFoundError as exc:
            raise _error(404, "not_found", str(exc)) from exc
        except UnsafeTargetError as exc:
            raise _error(422, "unsafe_target", str(exc)) from exc

    @app.post(
        "/v1/events",
        response_model=EventAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=secured,
        tags=["events"],
    )
    def publish_event(data: EventCreate) -> EventAccepted:
        try:
            event, created, count = service.publish_event(data)
        except IdempotencyConflict as exc:
            raise _error(409, "idempotency_conflict", str(exc)) from exc
        return EventAccepted(id=event.id, duplicate=not created, delivery_count=count)

    @app.get(
        "/v1/events/{event_id}",
        response_model=EventRead,
        dependencies=secured,
        tags=["events"],
    )
    def get_event(event_id: str) -> EventRead:
        try:
            return EventRead.model_validate(service.get_event(event_id))
        except NotFoundError as exc:
            raise _error(404, "not_found", str(exc)) from exc

    @app.get(
        "/v1/deliveries",
        response_model=list[DeliveryRead],
        dependencies=secured,
        tags=["deliveries"],
    )
    def list_deliveries(
        state_filter: Annotated[DeliveryState | None, Query(alias="state")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[DeliveryRead]:
        return [
            DeliveryRead.model_validate(item)
            for item in service.list_deliveries(state_filter, limit=limit)
        ]

    @app.get(
        "/v1/deliveries/{delivery_id}",
        response_model=DeliveryRead,
        dependencies=secured,
        tags=["deliveries"],
    )
    def get_delivery(delivery_id: str) -> DeliveryRead:
        try:
            return DeliveryRead.model_validate(service.get_delivery(delivery_id))
        except NotFoundError as exc:
            raise _error(404, "not_found", str(exc)) from exc

    @app.post(
        "/v1/deliveries/{delivery_id}/replay",
        response_model=DeliveryRead,
        dependencies=secured,
        tags=["deliveries"],
    )
    def replay_delivery(delivery_id: str) -> DeliveryRead:
        try:
            return DeliveryRead.model_validate(service.replay_delivery(delivery_id))
        except NotFoundError as exc:
            raise _error(404, "not_found", str(exc)) from exc
        except InvalidStateError as exc:
            raise _error(409, "invalid_state", str(exc)) from exc

    return app


app = create_app()
