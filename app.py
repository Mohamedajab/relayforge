"""Vercel entrypoint for the public, disposable demonstration only."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from relayforge.public_demo import app

public = Path(__file__).parent / "public"
if public.is_dir():
    app.mount("/", StaticFiles(directory=public, html=True))

__all__ = ["app"]
