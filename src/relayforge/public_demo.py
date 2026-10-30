"""Public sample runner. It exposes none of the production API's write endpoints."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from relayforge.demo_scenario import recovery_steps

app = FastAPI(title="RelayForge public demo", docs_url=None, redoc_url=None, openapi_url=None)
_running_until = 0.0
_next_run = 0.0


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": "disposable-demo"}


@app.post("/api/run", response_model=None)
async def run(request: Request) -> StreamingResponse | JSONResponse:
    global _running_until, _next_run
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"error": "Use this demo from its own page"}, status_code=403)
    if request.query_params or request.headers.get("content-length", "0") != "0":
        return JSONResponse(
            {"error": "This endpoint accepts only the fixed sample run"}, status_code=400
        )
    if time.monotonic() < max(_running_until, _next_run):
        return JSONResponse(
            {"error": "A demo is already running. Try again shortly."},
            status_code=429,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    _running_until = time.monotonic() + 20
    _next_run = time.monotonic() + 10

    async def events() -> AsyncIterator[str]:
        global _running_until
        try:
            async with asyncio.timeout(15):
                async for event in recovery_steps():
                    yield "data: " + json.dumps(event) + "\n\n"
        except Exception:
            logging.getLogger(__name__).exception("public_demo_failed")
            yield (
                "data: "
                + json.dumps(
                    {"stage": "error", "message": "The run could not finish. Try again shortly."}
                )
                + "\n\n"
            )
        finally:
            _running_until = 0.0

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
