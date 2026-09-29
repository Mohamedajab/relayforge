# ADR 0001: Database-backed delivery leases

Status: accepted

## Context

Webhook work must survive API restarts and worker crashes. Multiple workers should increase
throughput without sending the same due delivery concurrently under normal operation. The project
must also remain easy to evaluate locally without requiring a broker.

## Decision

Store deliveries in the SQL database and claim them with conditional updates. A claim records a
worker owner and an expiry time. Network I/O happens outside the claim transaction. Expired
in-flight rows become eligible for a new claim. Finalisation succeeds only for the current lease
owner and expected attempt count.

## Consequences

Positive:

- The database is the only required stateful dependency.
- Queue state is directly inspectable and queryable by operators.
- Crash recovery follows from persisted lease expiry.
- The same model works in SQLite tests and PostgreSQL deployment.
- No transaction or row lock is held during remote network I/O.

Negative:

- Polling creates some idle database load and delivery latency.
- The conditional-claim loop is not optimised for very large queues.
- At-least-once semantics still allow duplicates after ambiguous network outcomes.
- Database and queue workload share capacity.

## Alternatives considered

Redis streams or a hosted queue would provide efficient blocking consumption but introduce a
second persistence model and more local setup. A traditional task framework would reduce code but
hide the lease and recovery behaviour this project is intended to demonstrate. Holding row locks
during delivery would simplify exclusivity but couple database health to subscriber latency and is
therefore rejected.

## Follow-up

Measure claim latency and database load before changing architecture. If polling becomes material,
add PostgreSQL notifications as a wake-up hint while keeping the database row as the source of
truth. Introduce a broker only when workload measurements establish a need.

