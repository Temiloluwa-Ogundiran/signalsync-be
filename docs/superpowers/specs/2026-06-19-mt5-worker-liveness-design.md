# MT5 Worker Liveness Design

## Goal

Distinguish a healthy but busy MT5 worker from an unavailable worker without
exposing queue-pressure messages to users.

## Root Cause

The MT5 API creates durable jobs with status `queued`. When the Redis-backed
worker claims a job, it writes `running` only to Redis and does not persist that
transition to Postgres. The job-status API reads Postgres, so callers observe
`queued` throughout processing and then see only the terminal status. A
processing deadline therefore cannot distinguish queue delay from active MT5
work.

The public API health endpoint also returned a Cloudflare 522 during diagnosis,
so worker unavailability must be represented explicitly rather than treated as
ordinary queue pressure.

## Design

### Worker heartbeat

The MT5 worker publishes a heartbeat to Redis every second from a background
thread. The heartbeat key expires after five seconds. Because the publisher is
independent of job processing, a long-running job does not make a healthy worker
appear unavailable.

### Durable job lifecycle

Immediately after claiming a Redis job, the worker writes the `running` status
and `started_at` timestamp to both Redis and Postgres. Success and failure retain
their existing durable writes.

### Job status contract

The MT5 job-status response includes worker availability. The backend handles
states as follows:

- `queued` with a healthy heartbeat: wait without consuming the processing
  deadline.
- `queued` with a stale heartbeat: stop and return HTTP 503 with exactly
  `Service Error`.
- `running`: start the four-second processing deadline.
- `succeeded`: return the verified account result.
- `failed`: return the MT5 failure immediately.

The frontend continues waiting for the backend response and displays the API
detail without a service-busy message.

## Failure Semantics

The five-second heartbeat TTL is the unavailable-worker detection window. A
worker that has not published within that window is unavailable, not busy. The
user-facing response is intentionally generic: `Service Error`.

Transport failures between the backend and MT5 API retain the existing service
error handling. Invalid credentials remain a distinct authorization failure.

## Verification

- A worker runtime test proves `running` is persisted before dispatch.
- Store tests prove heartbeat publication and expiry semantics.
- Job-service tests prove status responses expose worker availability.
- Backend tests prove queued healthy jobs wait beyond the processing timeout,
  queued unavailable jobs return `Service Error`, and running jobs retain the
  four-second deadline.
- The full worker suite, focused backend suite, frontend lint, and frontend
  production build run before deployment.
