from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./relayforge.db"
    api_key: str = "development-key-change-me"
    allow_private_targets: bool = False
    allow_insecure_http: bool = False
    max_attempts: int = 6
    base_retry_seconds: float = 2.0
    max_retry_seconds: float = 300.0
    request_timeout_seconds: float = 10.0
    lease_seconds: int = 30
    response_body_limit: int = 2_048

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            database_url=os.getenv("RELAYFORGE_DATABASE_URL", defaults.database_url),
            api_key=os.getenv("RELAYFORGE_API_KEY", defaults.api_key),
            allow_private_targets=_as_bool(os.getenv("RELAYFORGE_ALLOW_PRIVATE_TARGETS", "false")),
            allow_insecure_http=_as_bool(os.getenv("RELAYFORGE_ALLOW_INSECURE_HTTP", "false")),
            max_attempts=int(os.getenv("RELAYFORGE_MAX_ATTEMPTS", str(defaults.max_attempts))),
            base_retry_seconds=float(
                os.getenv("RELAYFORGE_BASE_RETRY_SECONDS", str(defaults.base_retry_seconds))
            ),
            max_retry_seconds=float(
                os.getenv("RELAYFORGE_MAX_RETRY_SECONDS", str(defaults.max_retry_seconds))
            ),
            request_timeout_seconds=float(
                os.getenv(
                    "RELAYFORGE_REQUEST_TIMEOUT_SECONDS",
                    str(defaults.request_timeout_seconds),
                )
            ),
            lease_seconds=int(os.getenv("RELAYFORGE_LEASE_SECONDS", str(defaults.lease_seconds))),
        )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_retry_seconds <= 0 or self.max_retry_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if self.base_retry_seconds > self.max_retry_seconds:
            raise ValueError("base retry delay cannot exceed maximum retry delay")
        if self.lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
