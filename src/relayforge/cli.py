from __future__ import annotations

import asyncio
from typing import Any

import typer
from alembic import command
from alembic.config import Config

from relayforge.config import Settings
from relayforge.database import create_database_engine, create_schema, create_session_factory
from relayforge.logging import configure_logging
from relayforge.worker import DeliveryWorker

app = typer.Typer(
    no_args_is_help=True,
    help="Operate the RelayForge API, database and delivery workers.",
)


def _runtime() -> tuple[Settings, Any, Any]:
    settings = Settings.from_env()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    return settings, engine, factory


@app.command("init-db")
def init_db() -> None:
    """Create the schema for local development."""
    _, engine, _ = _runtime()
    create_schema(engine)
    typer.echo("RelayForge schema is ready.")


@app.command()
def migrate() -> None:
    """Apply all pending database migrations."""
    settings = Settings.from_env()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    typer.echo("RelayForge migrations are up to date.")


@app.command()
def api(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, min=1, max=65535),
    reload: bool = typer.Option(False, help="Enable development auto-reload."),
) -> None:
    """Run the HTTP API."""
    import uvicorn

    configure_logging()
    uvicorn.run("relayforge.api:app", host=host, port=port, reload=reload)


@app.command()
def worker(
    once: bool = typer.Option(False, help="Drain one batch and exit."),
    batch_size: int = typer.Option(25, min=1, max=500),
    concurrency: int = typer.Option(8, min=1, max=100),
    poll_seconds: float = typer.Option(1.0, min=0.05),
) -> None:
    """Deliver queued webhooks."""
    configure_logging()
    settings, _, factory = _runtime()
    delivery_worker = DeliveryWorker(factory, settings)
    if once:
        processed = asyncio.run(
            delivery_worker.run_once(batch_size=batch_size, concurrency=concurrency)
        )
        typer.echo(f"Processed {processed} delivery job(s).")
    else:
        try:
            asyncio.run(
                delivery_worker.run_forever(
                    poll_seconds=poll_seconds,
                    batch_size=batch_size,
                    concurrency=concurrency,
                )
            )
        except KeyboardInterrupt:
            typer.echo("Worker stopped.")


if __name__ == "__main__":
    app()
