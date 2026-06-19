# MT5 Verification Timing Design

## Goal

Return a definitive MT5 account verification result in under five seconds after
the verification job is admitted, while allowing temporary queue pressure to
wait silently instead of returning a service-busy response.

## Root Cause

The API currently combines a four-second hard deadline with a two-second job
poll interval. A valid MT5 job that completes around three seconds is observed
as running at zero and two seconds, then the API deadline expires before the
next poll at four seconds. The fast worker path is also not yet merged into the
worker's main branch.

## Design

Queue admission and MT5 processing use separate timing phases:

1. Submission retries HTTP 429 and 503 responses silently until the job is
   admitted. This phase is not reported to users as service busy.
2. Once admitted, verification is polled every 100 milliseconds with a
   four-second processing deadline.
3. The worker performs one MT5 initialization attempt capped at three seconds
   and does not restart the terminal for verification jobs.
4. The browser allows enough transport margin for the API response and does not
   impose a shorter deadline than the server. Normal admitted requests still
   complete in under five seconds; queue waiting is the explicit exception.

History import remains asynchronous and all non-verification worker jobs retain
their existing recovery behavior.

## Error Handling

- Invalid credentials return the MT5 rejection immediately.
- Processing that exceeds four seconds after admission returns HTTP 504.
- Queue pressure remains internal and continues waiting for admission.
- Transport and unexpected MT5-core failures retain their current API errors.

## Verification

- Add a backend regression test where a job succeeds after three seconds and
  assert that fast polling observes it before the deadline.
- Keep coverage proving temporary admission pressure is retried silently.
- Keep worker tests proving verification uses one attempt without restart.
- Run focused backend tests, the full MT5 worker suite, frontend lint, and the
  production frontend build.
- Replay the original three-second timing harness after implementation.

