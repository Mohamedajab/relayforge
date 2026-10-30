# Verification and CV notes

Local run: 30 October 2026, Windows, Python 3.12.10, HTTPX 0.28.1.

- `python -m pytest -o addopts="--cov=relayforge --cov-report=term --cov-fail-under=85" -q`: 46 passed, one PostgreSQL test skipped; 89.76% coverage.
- `ruff check .`, `ruff format --check .`, `mypy src`: passed.
- `python scripts/recovery_demo.py`: passed; terminal recording in `evidence/recovery.cast`.

The HTTPS regression uses a real local TLS socket: a certificate for receiver.test succeeds,
wrong.test fails on the same IP, and an unusable environment proxy is ignored. Its certificate
and private key are public test fixtures, valid only for the local receiver.test name.
Other regressions cover IPv6 pinning, mixed unsafe DNS answers, changed answers, off-loop resolution
and redirect rejection. The local demo uses SQLite and an in-process receiver, not a deployed service.

Docker's engine was unavailable locally. The existing CI job provisions PostgreSQL 17, applies
migrations and runs the competing-worker test on Python 3.11 and 3.12. Its result must be checked
on the PR; the skipped local test is not PostgreSQL evidence.

## Suggested CV bullets

- Built a Python webhook service with durable worker leases, signed requests, retries and recoverable failed deliveries.
- Added checked-IP delivery with original-host TLS verification and regression tests for DNS changes, redirects and certificate mismatches.
