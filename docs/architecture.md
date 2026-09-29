# Architecture

## Goals

RelayForge accepts events quickly and delivers them to zero or more HTTP subscribers without
pretending the network is reliable. Its primary goals are durable state, explainable failure
handling, horizontally scalable workers and an operator path for every terminal state.

The first release intentionally does not target very high throughput. It favours a compact system
whose concurrency and recovery rules can be inspected in one repository.

## Components

### API process

The FastAPI process validates and authenticates management requests, registers endpoints, accepts
events, creates endpoint-specific delivery rows and exposes status. It never performs subscriber
network I/O during event ingestion, so a slow subscriber cannot hold open a producer request.

### SQL database

The database is both the source of truth and the durable queue. Events preserve the accepted
producer payload. Deliveries hold current state, retry timing and lease ownership. Attempts are
append-only observations of network work. Audit entries capture control-plane actions.

PostgreSQL is the deployment database. SQLite keeps local evaluation friction low and is used by
the automated suite.

### Worker process

A worker claims a bounded batch, then executes HTTP requests concurrently under an asyncio
semaphore. The database transaction ends before network I/O starts. Each result is committed in a
new short transaction.

## Data model

- Endpoint: subscriber URL, signing secret, optional event-type filter and active flag.
- Event: source, type, idempotency key, canonical payload digest and JSON payload.
- Delivery: the many-to-many projection from one event to one matching endpoint.
- DeliveryAttempt: immutable timing, status and error evidence for one network attempt.
- AuditEntry: immutable application-level state transition record.

The unique constraint on event source plus idempotency key provides producer deduplication. A
second unique constraint on event plus endpoint prevents duplicate fan-out rows.

## Delivery state machine

    pending ------claim------> in_flight ------2xx------> succeeded
       ^                          |
       |                          +------transient failure------> retrying
       |                                                               |
       +---------------------------- due time --------------------------+

    in_flight -- expired lease --> eligible for a new claim
    in_flight -- permanent failure or exhausted attempts --> dead
    dead -- operator replay --> pending

Succeeded is terminal. Dead is terminal until an explicit replay. Replay does not erase attempt
history or reset the attempt counter, so the operational record remains honest.

## Claim algorithm

1. Select a small ordered set of due delivery IDs.
2. For each candidate, issue a conditional update that repeats the eligibility predicate.
3. Only a row updated by that statement is considered claimed.
4. Set state to in_flight, worker owner and lease expiry in the same update.
5. Commit before making any HTTP request.

The conditional update is the concurrency boundary. Two workers may select the same candidate, but
only one can update a row that still satisfies the predicate. The second update observes the new
in-flight state and affects zero rows.

Before finalising, a worker checks state, owner and attempt count. This prevents a slow, stale
worker from overwriting the result of a worker that reclaimed an expired lease.

## Delivery semantics

RelayForge provides at-least-once delivery. It cannot promise exactly once across an HTTP boundary:
a subscriber may process a request successfully while the response is lost. Retrying is therefore
correct, and consumers must deduplicate with the stable delivery or event ID.

Producer idempotency is scoped by source. Repeating the same key and content returns the original
event. Repeating the key with changed content is a conflict rather than silently accepting an
ambiguous write.

## Retry policy

Timeouts, transport failures, HTTP 408, 425, 429 and 5xx responses are transient. The next delay is
sampled uniformly between zero and an exponentially increasing cap. This full-jitter policy avoids
synchronised retry waves. A valid Retry-After value is honoured up to the configured maximum.

Redirects are not followed. Other 3xx and 4xx responses are treated as permanent failures and
dead-lettered immediately.

## Scaling path

- Run multiple stateless API processes behind a load balancer.
- Run multiple workers with distinct owner IDs against PostgreSQL.
- Tune batch size and per-worker concurrency independently.
- Partition or archive old attempts and audit rows when retention grows.
- Replace database polling with PostgreSQL notifications or a broker only when measurements justify
  the added operational dependency.
- Export traces and histograms through OpenTelemetry for production latency analysis.

## Verification strategy

Pure functions cover canonical JSON, signatures, retry classification and timing. Service tests
exercise uniqueness, fan-out and leasing against a real SQLite database. API tests use FastAPI's
test client. Worker tests use HTTPX's in-process mock transport while preserving real signing,
state transitions and attempt persistence.

