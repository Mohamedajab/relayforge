from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from relayforge.clock import utc_now
from relayforge.config import Settings
from relayforge.models import DeliveryState
from relayforge.retry import RetryPolicy
from relayforge.schemas import EventCreate
from relayforge.security import TargetValidator, verify_signature
from relayforge.service import RelayService
from relayforge.worker import DeliveryWorker


def order_event(key: str) -> EventCreate:
    return EventCreate(
        source="checkout",
        event_type="order.created",
        idempotency_key=key,
        payload={"order_id": key},
    )


@pytest.mark.asyncio
async def test_worker_retries_then_succeeds_with_valid_signature(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    secret = "worker-secret-at-least-16"
    make_endpoint(secret=secret)
    service.publish_event(order_event("worker-1"))
    statuses = iter([503, 204])
    seen_signatures: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamp = int(request.headers["X-RelayForge-Timestamp"])
        seen_signatures.append(
            verify_signature(
                secret,
                timestamp,
                request.content,
                request.headers["X-RelayForge-Signature"],
            )
        )
        return httpx.Response(next(statuses), headers={"Retry-After": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="worker-test",
            client=client,
            target_validator=validator,
            retry_policy=RetryPolicy(
                base_seconds=1, max_seconds=10, jitter=lambda _low, high: high
            ),
        )
        assert await worker.run_once() == 1
        retrying = service.list_deliveries()[0]
        assert retrying.state == DeliveryState.RETRYING
        assert retrying.attempt_count == 1
        assert retrying.last_status_code == 503

        with factory() as session:
            stored = session.get(type(retrying), retrying.id)
            assert stored is not None
            stored.next_attempt_at = utc_now() - timedelta(seconds=1)
            session.commit()

        assert await worker.run_once() == 1

    delivered = service.get_delivery(retrying.id)
    assert delivered.state == DeliveryState.SUCCEEDED
    assert delivered.attempt_count == 2
    assert delivered.last_status_code == 204
    assert len(delivered.attempts) == 2
    assert seen_signatures == [True, True]


@pytest.mark.asyncio
async def test_non_retryable_response_goes_to_dead_letter(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    make_endpoint()
    service.publish_event(order_event("worker-400"))

    transport = httpx.MockTransport(lambda _request: httpx.Response(400, text="invalid"))
    async with httpx.AsyncClient(transport=transport) as client:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="worker-dead",
            client=client,
            target_validator=validator,
        )
        assert await worker.run_once() == 1

    delivery = service.list_deliveries()[0]
    assert delivery.state == DeliveryState.DEAD
    assert delivery.attempt_count == 1
    assert delivery.last_error == "HTTP 400"


@pytest.mark.asyncio
async def test_stale_worker_cannot_finalize_reclaimed_delivery(
    service: RelayService,
    make_endpoint: object,
    factory: sessionmaker[Session],
    settings: Settings,
    validator: TargetValidator,
) -> None:
    make_endpoint()
    service.publish_event(order_event("stale-1"))
    delivery_id = service.claim_due("other-worker")[0]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    try:
        worker = DeliveryWorker(
            factory,
            settings,
            owner="stale-worker",
            client=client,
            target_validator=validator,
        )
        assert await worker.process(delivery_id, client) is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_pins_each_attempt_and_never_follows_redirects(
    service, make_endpoint, factory, settings, validator
):
    make_endpoint(url="https://receiver.test:8443/callback")
    service.publish_event(order_event("pin"))
    answers = iter([["2606:4700:4700::1111"], ["127.0.0.1"]])
    checked = TargetValidator(resolver=lambda _host, _port: next(answers))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        worker = DeliveryWorker(
            factory, settings, owner="pinned", client=client, target_validator=checked
        )
        assert await worker.run_once() == 1
        assert len(requests) == 1
        assert requests[0].url.host == "2606:4700:4700::1111"
        assert requests[0].headers["Host"] == "receiver.test:8443"
        assert requests[0].extensions["sni_hostname"] == "receiver.test"
        assert service.list_deliveries()[0].state == DeliveryState.DEAD
        service.publish_event(order_event("rebound"))
        assert await worker.run_once() == 1
        assert len(requests) == 1
        assert "unsafe target" in service.list_deliveries()[0].last_error


@pytest.mark.asyncio
async def test_worker_resolves_off_the_event_loop(service, make_endpoint, factory, settings):
    import threading

    make_endpoint()
    service.publish_event(order_event("thread"))
    loop_thread = threading.get_ident()

    def resolver(_host, _port):
        assert threading.get_ident() != loop_thread
        return ["127.0.0.1"]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(204))
    ) as client:
        worker = DeliveryWorker(
            factory,
            settings,
            client=client,
            target_validator=TargetValidator(
                allow_private=True, allow_insecure_http=True, resolver=resolver
            ),
        )
        assert await worker.run_once() == 1


@pytest.mark.asyncio
async def test_pinned_https_checks_hostname_and_ignores_proxy(
    service, make_endpoint, factory, settings, monkeypatch
):
    import ssl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from threading import Thread

    hosts = []
    names = []

    class Receiver(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            hosts.append(self.headers["Host"])
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    fixtures = Path(__file__).parent / "fixtures"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(fixtures / "receiver.pem", fixtures / "receiver.key")
    tls.set_servername_callback(lambda sock, name, ctx: names.append(name))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(fixtures / "receiver.pem"))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    # trust_env=False also ignores SSL_CERT_FILE: provide the test CA explicitly.
    original_client = httpx.AsyncClient
    ca = ssl.create_default_context(cafile=str(fixtures / "receiver.pem"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(verify=ca, **kwargs))
    validator = TargetValidator(allow_private=True, resolver=lambda host, port: ["127.0.0.1"])
    port = server.server_port
    try:
        make_endpoint(url=f"https://receiver.test:{port}/webhooks", name="valid")
        make_endpoint(url=f"https://wrong.test:{port}/webhooks", name="wrong")
        service.publish_event(order_event("tls"))
        worker = DeliveryWorker(factory, settings, target_validator=validator)
        assert await worker.run_once(concurrency=1) == 2
        deliveries = service.list_deliveries()
        assert sorted(d.state.value for d in deliveries) == ["retrying", "succeeded"]
        assert hosts == [f"receiver.test:{port}"]
        assert "receiver.test" in names
        assert "wrong.test" in names
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
