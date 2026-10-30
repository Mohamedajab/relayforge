# Verification and CV notes

Latest local run: 30 October 2026, Windows, Python 3.12.10, HTTPX 0.28.1.

- `python -m pytest`: 52 passed, one PostgreSQL test skipped; 90.56% coverage.
- `ruff check .`, `ruff format --check .`, `mypy src`: passed.
- `python scripts/recovery_demo.py`: passed; terminal recording in `evidence/recovery.cast`.

The HTTPS regression uses a real local TLS socket: a certificate for receiver.test succeeds,
wrong.test fails on the same IP, and an unusable environment proxy is ignored. Its certificate
and private key are public test fixtures, valid only for the local receiver.test name.
Other regressions cover IPv6 pinning, mixed unsafe DNS answers, changed answers, off-loop resolution
and redirect rejection. The [public recovery demo](public-demo.md) uses temporary SQLite
and an in-process receiver. It does not host the persistent PostgreSQL queue or production API.

Docker's engine was unavailable locally. The existing CI job provisions PostgreSQL 17, applies
migrations and runs the competing-worker test on Python 3.11 and 3.12.
[CI run 41139723690](https://github.com/Mohamedajab/relayforge/actions/runs/41139723690)
passed all 53 tests with no skips on both versions, including real PostgreSQL;
Python 3.12 coverage was 90.67%.

## Suggested CV bullets

- Built a Python webhook service with durable worker leases, signed requests, retries and recoverable failed deliveries.
- Added checked-IP delivery with original-host TLS verification and regression tests for DNS changes, redirects and certificate mismatches.
