# Threat model

## Assets

- Endpoint signing secrets
- Event payloads, which may contain commercially sensitive data
- API credentials
- Internal network reachability from worker hosts
- Delivery and audit history

## Trust boundaries

Producers cross the management API boundary. Endpoint URLs and event payloads are untrusted input.
Workers cross the public network boundary when connecting to subscriber infrastructure. Operators
and the database are trusted for this version.

## Threats and controls

| Threat | Control |
| --- | --- |
| Unauthorised management calls | Constant-time comparison of an X-API-Key value |
| Duplicate producer submissions | Source-scoped idempotency uniqueness plus payload digest check |
| Payload modification in transit | Per-endpoint HMAC-SHA256 signature over timestamp and exact body |
| Replay of captured webhook | Stable IDs and timestamp header; consumer must enforce an age window |
| SSRF to loopback or internal services | Scheme validation, DNS resolution, IP classification and checks before every attempt |
| Redirect-based SSRF bypass | HTTP redirects are disabled |
| Credentials leaked through URL | User information in endpoint URLs is rejected |
| Unbounded retained response data | Subscriber bodies are truncated before persistence |
| Worker result races | Lease-owner and attempt-count checks before finalisation |
| Retry storm | Exponential cap, full jitter, Retry-After support and bounded concurrency |
| Secret exposure through read API | Signing secret is returned only during endpoint creation |
| Silent operational changes | Append-only audit records for control and delivery transitions |

## Residual risks

### DNS rebinding

RelayForge validates every DNS answer before delivery, but HTTPX performs its own resolution when
connecting. An attacker who changes the answer between those steps may bypass the address check.
A hardened version should use a transport that connects to the validated address while retaining
the original TLS server name.

### Secret storage

Endpoint secrets are currently plaintext database fields. Production use requires envelope
encryption with a cloud KMS, key-version metadata, rotation and access logging.

### API authentication

A single configured API key is deliberately small in scope. A multi-tenant service requires
short-lived identity tokens, scoped roles, key rotation and per-principal audit identity.

### Resource exhaustion

Pydantic constrains identifiers and subscription lists, but payload byte size and request rate
should also be enforced at the reverse proxy and application layers. Database retention and
per-source quotas are future controls.

### Subscriber response content

Response bodies may contain secrets supplied by a subscriber. They are truncated but not redacted
or encrypted. Production policy should normally disable body retention or apply explicit
classification and encryption.

## Consumer verification checklist

1. Read the raw request body before JSON parsing.
2. Recompute the signature using the timestamp, a period and the exact bytes.
3. Compare signatures in constant time.
4. Reject timestamps outside a short allowed window.
5. Deduplicate the stable delivery ID before applying side effects.
6. Return 2xx only after the durable consumer transaction commits.

