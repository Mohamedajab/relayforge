import json
import time

import httpx
import pytest

from relayforge import public_demo


@pytest.fixture(autouse=True)
def reset_admission(monkeypatch):
    monkeypatch.setattr(public_demo, "_running_until", 0.0)
    monkeypatch.setattr(public_demo, "_next_run", 0.0)


async def test_public_run_recovers_and_verifies_one_delivery():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_demo.app), base_url="https://demo.test"
    ) as client:
        assert (await client.get("/healthz")).json()["mode"] == "disposable-demo"
        response = await client.post("/api/run", headers={"Origin": "https://demo.test"})
        assert response.status_code == 200
        events = [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n")]
        assert [event["status"] for event in events if event["stage"] == "attempt"] == [
            503,
            503,
            202,
        ]
        assert events[-1]["stage"] == "verified"
        assert events[-1]["received"] == events[-1]["deliveries"] == 1
        assert public_demo._running_until == 0
        limited = await client.post("/api/run")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "10"


@pytest.mark.parametrize(
    ("url", "kwargs", "status"),
    [
        ("/api/run?target=other", {}, 400),
        ("/api/run", {"content": b"other"}, 400),
        ("/api/run", {"headers": {"Origin": "https://other.test"}}, 403),
    ],
)
async def test_public_run_rejects_custom_input(url, kwargs, status):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_demo.app), base_url="https://demo.test"
    ) as client:
        assert (await client.post(url, **kwargs)).status_code == status
        assert public_demo._running_until == 0


async def test_running_demo_rejects_another_request(monkeypatch):
    monkeypatch.setattr(public_demo, "_running_until", time.monotonic() + 20)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_demo.app), base_url="https://demo.test"
    ) as client:
        assert (await client.post("/api/run")).status_code == 429


async def test_failed_run_releases_admission_without_exposing_exception(monkeypatch):
    async def broken_steps():
        raise RuntimeError("private exception details")
        yield

    monkeypatch.setattr(public_demo, "recovery_steps", broken_steps)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_demo.app), base_url="https://demo.test"
    ) as client:
        response = await client.post("/api/run")
    assert '"stage": "error"' in response.text
    assert "private exception details" not in response.text
    assert public_demo._running_until == 0
