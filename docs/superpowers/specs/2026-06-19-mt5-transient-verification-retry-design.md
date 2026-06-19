# MT5 Transient Verification Retry Design

## Goal

Eliminate false authorization failures when a valid account's first MT5
verification job fails because the Wine/MT5 IPC channel is still warming.

## Production Evidence

Railway logs show the same XM account first failing after 6.08 seconds with
`IPC timeout (code=-10005)` and then succeeding on the next request in 2.10
seconds. Deriv and other brokers show the same first-attempt IPC failure.

The backend currently maps every failed verification job to the invalid
credentials response, even though MT5 authorization failures use the distinct
code `-6`.

## Design

The backend MT5 client classifies failed jobs before exposing them to account
connection logic:

- Errors containing transport codes `-10005`, `-10004`, or `-10003`, or the
  corresponding IPC/pipe/connection wording, are transient.
- Authorization code `-6` and all other ordinary job failures are definitive.

`verify_credentials` may submit at most two jobs. If the first job fails with a
transient transport error, it immediately submits one fresh verification job.
The second job receives its own queue wait and processing deadline. No terminal
restart occurs and no additional attempts are allowed.

## User-Facing Results

- First transport failure followed by success: return the verified account in
  the original browser request.
- Two transport failures: return HTTP 503 with exactly `Service Error`.
- MT5 authorization failure: return the existing invalid-credentials message
  immediately without retry.
- Healthy queue pressure continues waiting silently.

## Verification

- A client test reproduces `-10005` on the first job and success on the second,
  asserting exactly two submissions.
- A client test proves two transient failures stop after two submissions.
- A client test proves MT5 `-6` performs only one submission.
- An account-service test proves an exhausted transient failure maps to HTTP
  503 `Service Error`.
- Focused backend tests and the production timing script run before deployment.
