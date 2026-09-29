# RelayForge

RelayForge is a durable webhook delivery service built to make failure visible and recoverable.
Producers publish an event once; RelayForge fans it out to subscribed endpoints, signs each
request, retries transient failures with jittered exponential backoff, and moves exhausted or
permanent failures to a replayable dead-letter state.

This is a portfolio project, but it is designed around production concerns rather than demo-only
CRUD: competing workers, process crashes, duplicate requests, unsafe callback URLs, auditability,
operator recovery and deterministic tests.

## What it demonstrates

- Python 3.11+, FastAPI, Pydantic, SQLAlchemy 2 and Alembic
- Durable background work with atomic claims and expiring worker leases
- At-least-once delivery, producer idempotency and endpoint-level deduplication
- Async HTTP I/O with bounded concurrency
- HMAC-SHA256 request signing over canonical JSON
- Full-jitter exponential backoff, Retry-After support and dead-letter replay
- SSRF-oriented target validation and redirect blocking
- Structured logs, request IDs, readiness checks and Prometheus-format metrics
- Unit, API and worker integration tests with mocked transports
- Docker Compose, non-root containers and GitHub Actions quality gates

## Architecture

    Producer
       |
       | POST /v1/events + idempotency key
       v
    +-------------+       atomic claim + lease       +------------------+
    | FastAPI API | --------------------------------> | Delivery workers |
    +------+------+                                   +--------+---------+
           |                                                   |
           | event, delivery and audit rows                    | signed POST
           v                                                   v
    +-------------+                                    Subscriber endpoints
    | SQL database|
    +-------------+
           ^
           | delivery status, attempts, replay
           |
       Operator API / metrics

The API and workers do not share in-memory queue state. A delivery is claimed with a conditional
database update and a time-limited lease. If a worker dies after claiming work, another worker can
reclaim it after the lease expires. Finalisation checks lease ownership so a stale worker cannot
overwrite a newer result.

## Run locally

Create the environment and install the application:

    python -m venv .venv
    .venv/Scripts/activate
    python -m pip install -e ".[dev]"

On macOS or Linux, activate with:

    source .venv/bin/activate

Set a non-default API key, apply the migrations, then start one API process and one worker:

    set RELAYFORGE_API_KEY=local-development-key
    relayforge migrate
    relayforge api
    relayforge worker

PowerShell users can set the key with:

    $env:RELAYFORGE_API_KEY = "local-development-key"

Interactive OpenAPI documentation is available at http://127.0.0.1:8000/docs.

### Register a subscriber

Public HTTPS destinations are required by default. For a local receiver, explicitly set
RELAYFORGE_ALLOW_PRIVATE_TARGETS=true and RELAYFORGE_ALLOW_INSECURE_HTTP=true.

    curl -X POST http://127.0.0.1:8000/v1/endpoints ^
      -H "Content-Type: application/json" ^
      -H "X-API-Key: local-development-key" ^
      -d "{\"name\":\"orders\",\"url\":\"https://example.com/webhooks\",\"event_types\":[\"order.created\"]}"

The signing secret is returned only by the create response. Store it securely.

### Publish an event

    curl -X POST http://127.0.0.1:8000/v1/events ^
      -H "Content-Type: application/json" ^
      -H "X-API-Key: local-development-key" ^
      -d "{\"source\":\"checkout\",\"event_type\":\"order.created\",\"idempotency_key\":\"checkout-ord-42-v1\",\"payload\":{\"order_id\":\"ord-42\",\"total\":1299}}"

Reusing the same source and idempotency key with the same event returns the original event.
Reusing it with a different type or payload returns HTTP 409.

## Delivery contract

Each request body is a stable JSON envelope with id, type, created_at and data fields. RelayForge
adds these headers:

| Header | Purpose |
| --- | --- |
| X-RelayForge-Event-Id | Stable producer event identifier |
| X-RelayForge-Delivery-Id | Stable endpoint-specific delivery identifier |
| X-RelayForge-Attempt | One-based attempt number |
| X-RelayForge-Timestamp | Unix timestamp included in the signature |
| X-RelayForge-Signature | v1 HMAC-SHA256 signature |

Consumers should verify the signature, reject stale timestamps and make processing idempotent using
the delivery or event ID. At-least-once delivery means duplicates remain possible after ambiguous
network failures.

## Failure policy

| Outcome | Action |
| --- | --- |
| 2xx | Mark succeeded |
| 408, 425, 429 or 5xx | Retry with full-jitter exponential backoff |
| Valid Retry-After | Wait at least that long, bounded by the configured maximum |
| Other 3xx or 4xx | Dead-letter immediately |
| Timeout or transport error | Retry |
| Unsafe target at delivery time | Dead-letter immediately |
| Attempts exhausted | Dead-letter |

Dead-lettered deliveries can be inspected and replayed through
POST /v1/deliveries/{delivery_id}/replay.

## Operations

- GET /healthz checks process liveness.
- GET /readyz verifies database connectivity.
- GET /metrics exposes queue depth by state and the persisted attempt count.
- X-Request-Id is accepted or generated and returned on every response.
- Audit rows record endpoint changes, event publication, claims, retry scheduling, success,
  dead-lettering and manual replay.

For local evaluation, SQLite is the zero-setup default. Docker Compose uses PostgreSQL and runs
one migration job plus separate API and worker containers.

    docker compose up --build

## Quality checks

    ruff check .
    ruff format --check .
    mypy src
    pytest

The test suite covers signing and tamper detection, SSRF rules, retry timing, subscription fan-out,
idempotency conflicts, lease expiry, stale-worker protection, API error contracts, signed delivery,
retry-to-success and dead-letter behaviour.

## Engineering decisions and limits

- Database-backed work keeps the failure model inspectable and avoids requiring a broker for a
  local evaluation. PostgreSQL is the intended multi-worker store; SQLite is for development.
- Conditional updates provide exclusive claims without holding a database transaction open during
  network I/O.
- Endpoint secrets are stored in the application database for this version. A production deployment
  should envelope-encrypt them with a cloud KMS and support rotation.
- DNS addresses are checked before each delivery and redirects are disabled. A hardened deployment
  should also pin the validated address at connection time to close DNS-rebinding races.
- Metrics are derived from persisted state, so they survive process restarts. High-volume deployments
  should export counters and histograms to a dedicated telemetry backend.

See docs/architecture.md, docs/threat-model.md and docs/adr/0001-database-backed-leases.md for the
detailed reasoning.

## Suggested CV wording

RelayForge - Python, FastAPI, SQLAlchemy, PostgreSQL, Docker

- Built a durable webhook platform with idempotent event ingestion, HMAC-signed delivery, leased
  multi-worker claims, bounded async concurrency and replayable dead-letter handling.
- Added full-jitter retries with Retry-After support, SSRF controls, audit history, Prometheus-format
  metrics, database migrations and automated API/worker integration tests.

## Licence

MIT
