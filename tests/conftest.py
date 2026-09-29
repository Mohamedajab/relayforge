from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from relayforge.config import Settings
from relayforge.database import create_database_engine, create_schema, create_session_factory
from relayforge.schemas import EndpointCreate
from relayforge.security import TargetValidator
from relayforge.service import RelayService


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        api_key="test-api-key",
        allow_private_targets=True,
        allow_insecure_http=True,
        max_attempts=3,
        base_retry_seconds=1,
        max_retry_seconds=10,
        request_timeout_seconds=1,
        lease_seconds=30,
    )


@pytest.fixture()
def factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_database_engine(settings.database_url)
    create_schema(engine)
    return create_session_factory(engine)


@pytest.fixture()
def validator() -> TargetValidator:
    return TargetValidator(
        allow_private=True,
        allow_insecure_http=True,
        resolver=lambda _host, _port: ["127.0.0.1"],
    )


@pytest.fixture()
def service(
    factory: sessionmaker[Session], settings: Settings, validator: TargetValidator
) -> RelayService:
    return RelayService(factory, settings, target_validator=validator)


@pytest.fixture()
def make_endpoint(service: RelayService) -> Callable[..., object]:
    def create(
        *,
        name: str = "orders",
        url: str = "http://webhook.test/callback",
        event_types: list[str] | None = None,
        secret: str = "test-secret-at-least-16",
    ) -> object:
        endpoint, _ = service.create_endpoint(
            EndpointCreate(
                name=name,
                url=url,
                event_types=event_types or [],
                secret=secret,
            )
        )
        return endpoint

    return create
