# Five Minute Evaluation Guide

This walkthrough demonstrates the failure case RelayForge is designed to handle: a subscriber is
temporarily unavailable, several workers are running, and the producer must not wait or publish the
same event again.

## Start the platform

The demo Compose overlay adds a receiver that verifies RelayForge signatures and fails the first
two delivery attempts with HTTP 503.

    docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build

Wait until the API reports ready:

    curl http://localhost:8000/readyz

## Register the receiver

    curl -X POST http://localhost:8000/v1/endpoints +      -H "Content-Type: application/json" +      -H "X-API-Key: local-development-key" +      -d '{"name":"demo-orders","url":"http://receiver:9000/webhooks/flaky","event_types":["order.created"],"secret":"local-receiver-secret"}'

RelayForge accepts private HTTP targets only because the demo overlay explicitly enables them.
The default configuration requires public HTTPS destinations.

## Publish an order event

    curl -X POST http://localhost:8000/v1/events +      -H "Content-Type: application/json" +      -H "X-API-Key: local-development-key" +      -d '{"source":"checkout","event_type":"order.created","idempotency_key":"demo-order-42","payload":{"order_id":"ord-42","total_minor":1299,"currency":"GBP"}}'

The API returns HTTP 202 immediately. The subscriber's availability does not extend the producer
request.

## Inspect recovery

Query the delivery state and attempt history:

    curl "http://localhost:8000/v1/deliveries?state=succeeded" +      -H "X-API-Key: local-development-key"

Inspect what the receiver processed:

    curl http://localhost:9000/received

The receiver returns 503 twice. RelayForge persists both attempts, honours Retry-After, retries and
then records a successful attempt. The receiver list contains one event because it deduplicates the
stable delivery ID.

## Verify producer idempotency

Publish the same event body and idempotency key again. RelayForge returns the original event ID with
duplicate set to true and creates no second delivery. Change the payload while retaining the key
and the API returns HTTP 409 because the request is ambiguous.

## Review the engineering evidence

- tests/test_postgres_integration.py proves exclusive claims against PostgreSQL with competing
  worker threads.
- tests/test_worker.py covers retry, success, dead-letter and stale-worker finalisation.
- tests/test_example_receiver.py covers signature validation, timestamp freshness and consumer
  deduplication.
- docs/architecture.md explains the state machine, lease boundary and at-least-once semantics.
- docs/threat-model.md records controls and unresolved production risks instead of claiming they do
  not exist.

