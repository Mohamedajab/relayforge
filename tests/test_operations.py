from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from relayforge.cli import app
from relayforge.config import Settings
from relayforge.logging import JsonFormatter, configure_logging


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYFORGE_DATABASE_URL", "sqlite:///custom.db")
    monkeypatch.setenv("RELAYFORGE_API_KEY", "configured")
    monkeypatch.setenv("RELAYFORGE_ALLOW_PRIVATE_TARGETS", "yes")
    monkeypatch.setenv("RELAYFORGE_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("RELAYFORGE_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("RELAYFORGE_BASE_RETRY_SECONDS", "3")
    monkeypatch.setenv("RELAYFORGE_MAX_RETRY_SECONDS", "20")
    monkeypatch.setenv("RELAYFORGE_REQUEST_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("RELAYFORGE_LEASE_SECONDS", "45")

    settings = Settings.from_env()

    assert settings.database_url == "sqlite:///custom.db"
    assert settings.api_key == "configured"
    assert settings.allow_private_targets is True
    assert settings.allow_insecure_http is True
    assert settings.max_attempts == 4
    assert settings.base_retry_seconds == 3
    assert settings.max_retry_seconds == 20
    assert settings.request_timeout_seconds == 7
    assert settings.lease_seconds == 45


@pytest.mark.parametrize(
    "values",
    [
        {"max_attempts": 0},
        {"base_retry_seconds": 0},
        {"base_retry_seconds": 20, "max_retry_seconds": 10},
        {"lease_seconds": 0},
    ],
)
def test_settings_reject_invalid_values(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Settings(**values)


def test_init_db_cli_creates_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "cli.db"
    monkeypatch.setenv("RELAYFORGE_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    result = CliRunner().invoke(app, ["init-db"])

    assert result.exit_code == 0
    assert "schema is ready" in result.stdout
    assert database_path.exists()


def test_json_logging_includes_context_and_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exception_info = sys.exc_info()
        record = logging.LogRecord(
            name="relayforge.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="delivery failed",
            args=(),
            exc_info=exception_info,
        )
    record.request_id = "req-1"
    payload = json.loads(formatter.format(record))

    assert payload["level"] == "error"
    assert payload["request_id"] == "req-1"
    assert "RuntimeError: boom" in payload["exception"]


def test_configure_logging_replaces_root_handlers() -> None:
    configure_logging(logging.DEBUG)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
