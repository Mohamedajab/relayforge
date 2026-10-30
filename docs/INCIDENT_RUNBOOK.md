# RelayForge local incident and recovery runbook

RelayForge is a durable, database-backed webhook delivery service. This
runbook describes **local and demo operation**, including what to inspect
when a delivery is delayed, rejected or apparently duplicated. It is not
a production incident-response SLA.

Read [architecture](architecture.md), [threat model](threat-model.md)
and the [five-minute demo](evaluation-guide.md) alongside this guide.

## First: determine whether the API or delivery worker is affected

The base Compose stack uses PostgreSQL 17, a one-off migration service,
an API service and a separate worker:

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 migrate api worker postgres
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8000/metrics
```

- `/healthz` is a process liveness check.
- `/readyz` checks database connectivity, not downstream subscriber health.
- `/metrics` exposes persisted queue depth by state and an attempt count;
  it is useful for identifying backlogs, not proof of delivery correctness.
- The API accepts and stores events **without making network calls to a
  subscriber**; an accepted producer request does not mean the webhook was
  received.

`docker-compose.demo.yml` additionally starts a receiver that deliberately
returns 503 on its first **two** attempts. Run the full demo with:

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

The overlay relaxes private-target and HTTP restrictions **for the demo**.
Never carry these allowances into a publicly reachable deployment.

## State-transition quick reference

| State | Meaning | Next check |
| --- | --- | --- |
| `pending` | Accepted, not yet successfully claimed | Worker status, queue age, eligible-at time |
| `in_flight` | Worker lease currently owns attempt | Lease expiry, worker logs and attempt history |
| `retrying` | Transient failure; scheduled for a later time | HTTP status, Retry-After, jitter/backoff |
| `succeeded` | Worker recorded HTTP 2xx | Check subscriber-side idempotency and audit history |
| `dead` | Permanent failure or exhausted retries | Review cause before deliberate replay |

A worker claim is an atomic conditional database update. The worker
commits the claim before network I/O. On an expired lease another worker
may reclaim an abandoned delivery; a stale worker must not overwrite
the newer result. A replay of `dead` transitions it to `pending` **without
erasing history or resetting the attempt counter**.

## Common symptoms

### API is healthy, but deliveries remain pending

Check that the worker is running and points at the same PostgreSQL
database as the API. Review its logs for claim/DB errors. A receiver
that is slow should not delay accepting a *new event* in the API, but it
can build a delivery backlog. Inspect the queue metrics and the
per-delivery attempt history before scaling workers.

### Delivery is retrying repeatedly

HTTP 408, 425, 429 and 5xx, timeouts and transport errors are treated
as transient. The retry delay uses bounded full-jitter exponential
backoff. Valid `Retry-After` instructions can extend the minimum delay
within the configured cap. Check the *latest* attempt and the next
eligible time; do not assume the worker has stopped merely because
nothing happened immediately.

### Delivery moved to dead

Other 3xx/4xx responses, unsafe destinations and exhausted retry
attempts are permanent under the configured policy. Confirm the
destination, authentication, TLS/HTTP status and subscriber behaviour
before any manual replay. **Do not** use replay to bypass an
SSRF-oriented destination check.

The documented replay route is
`POST /v1/deliveries/{delivery_id}/replay`. It requires management
authentication, and the operator should log why a replay is warranted.
A replay is not evidence of exactly-once processing.

### Subscriber reports duplicates

RelayForge intentionally provides **at-least-once** delivery. The
subscriber might complete a transaction but its 2xx response can be lost.
Consumers must verify the HMAC and timestamp and deduplicate by the
stable event/delivery ID **in durable storage before side effects**.
Avoid treating the `X-RelayForge-Attempt` number as the deduplication key.

### Event POST returns a conflict

Producer idempotency is scoped by `source` and `idempotency_key`.
The same key + same event body maps to the original event; a changed
event type or payload with the same key gives **HTTP 409**. Confirm the
producer's event identity before generating a new idempotency key;
blindly changing keys may create a duplicate business event.

## Inspect the demonstration state

The evaluation guide shows authenticated calls. An example for local
development (the key below is the **insecure Compose demo value**):

```bash
curl -H "X-API-Key: local-development-key" \
  "http://127.0.0.1:8000/v1/deliveries?state=succeeded"
curl "http://127.0.0.1:9000/received"
```

The second URL exists **only** with the demo overlay. It demonstrates
the receiver's idempotent processing of the stable delivery ID.

Use the recorded delivery/attempt/audit entries to investigate an
individual failure. Do not paste signing secrets, full sensitive
payloads or API credentials into logs or public issue reports.

## Safe shutdown and data retention

```bash
docker compose down
```

This stops containers but leaves the named PostgreSQL volume intact.
`docker compose down -v` is **destructive** and erases the local
`relayforge-data` volume. Preserve evidence before resetting a demo.

## Boundaries of this build

- The signing secret is returned at endpoint creation and stored in
  plaintext in the application DB. Production use needs KMS-backed
  encryption, rotation and access controls.
- Each attempt connects to a checked IP with the original TLS hostname.
  Redirects and environment proxies are disabled; connections are not reused.
- SQLite is suitable for local checks; PostgreSQL is the intended
  concurrent-worker store.
- No code here proves end-to-end exactly-once delivery, full network
  isolation, PCI compliance or a production-grade operations model.

The most useful verification tests are in
`tests/test_postgres_integration.py`, `tests/test_worker.py` and
`tests/test_example_receiver.py`. The docs describe their intended
coverage, not results of a newly executed test suite.

