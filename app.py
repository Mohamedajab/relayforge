"""Vercel entrypoint for the public, disposable demonstration only."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from relayforge.public_demo import app

app.mount("/", StaticFiles(directory=Path(__file__).parent / "public", html=True, check_dir=False))

__all__ = ["app"]
