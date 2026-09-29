from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from relayforge.models import DeliveryState


class EndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    event_types: list[str] = Field(default_factory=list, max_length=100)
    secret: str | None = Field(default=None, min_length=16, max_length=512)

    @field_validator("event_types")
    @classmethod
    def unique_event_types(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 160 for item in cleaned):
            raise ValueError("event types must contain 1 to 160 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("event types must be unique")
        return cleaned


class EndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    url: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EndpointCreated(EndpointRead):
    secret: str


class EndpointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    event_types: list[str] | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @field_validator("event_types")
    @classmethod
    def unique_event_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return EndpointCreate.unique_event_types(value)


class EventCreate(BaseModel):
    source: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    event_type: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._-]+$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]


class EventAccepted(BaseModel):
    id: str
    duplicate: bool
    delivery_count: int


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    event_type: str
    idempotency_key: str
    payload: dict[str, Any]
    created_at: datetime


class AttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt_number: int
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status_code: int | None
    error: str | None


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    endpoint_id: str
    state: DeliveryState
    attempt_count: int
    next_attempt_at: datetime
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    attempts: list[AttemptRead] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
