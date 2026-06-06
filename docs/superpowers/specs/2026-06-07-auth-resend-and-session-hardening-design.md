# Auth Resend and Session Hardening Design

## Summary

SyncTrades auth needs a clean-slate repair across `synctrades-be` and `synctrades-fe`.

Today the system has three related problems:

1. Backend verification email delivery still uses old SMTP-style plumbing and fallback behavior that no longer matches the desired deployment model.
2. Frontend protected routing is not authoritative enough, so anonymous users can reach `/` and briefly see dashboard UI before auth state settles.
3. Signup, signin, logout, and verification flows do not consistently reset client state, which can leave stale session or query state behind.

This design makes Resend the only email provider, moves route protection to server truth, and makes auth flows converge on one explicit experience:

1. Sign up
2. Verify email
3. Sign in
4. Land on `/journal`

## Goals

- Use Resend as the only email delivery mechanism in the backend
- Remove SMTP and other send-provider code, env vars, and dependencies
- Prevent unauthenticated users from seeing protected dashboard shells
- Make `/` auth-aware on the server
- Keep email verification required before first login
- Add resend-verification recovery directly into the frontend auth experience
- Ensure login/logout/auth-refresh transitions do not leave stale query or UI state behind

## Non-Goals

- Redesign unrelated dashboard pages
- Change token model from access token + rotated refresh token
- Introduce social login or OAuth providers
- Refactor unrelated frontend data hooks unless required for auth correctness

## Current Problems

### Backend

- Verification email sending is implemented through `fastapi-mail` SMTP configuration.
- If SMTP is missing, the backend silently logs a verification link instead of failing the request path honestly.
- `.env.example`, compose, and config still expose SMTP-era variables that the desired deployment should no longer use.

### Frontend

- `/` always redirects to `/journal` regardless of auth state.
- The dashboard route group layout is fully client-side, so it can render before a reliable server auth decision has been enforced.
- Auth-sensitive query state is not explicitly cleared on logout and is only partially refreshed on token changes.
- Login success routes to `/overview` while the desired default app landing page is `/journal`.

## Desired User Experience

### Unauthenticated

- Visiting `/` redirects to `/login`
- Visiting any protected dashboard route redirects to `/login` before protected UI renders
- Visiting `/login` or `/register` shows auth pages normally

### Authenticated

- Visiting `/` redirects to `/journal`
- Visiting `/login` or `/register` redirects to `/journal`
- Visiting protected routes renders immediately without anonymous fallback shells

### Signup and Verification

- Register creates the account and sends a verification email through Resend
- Registration does not create a signed-in session
- The user is sent to `/login` with a success cue explaining that verification is required
- If login is attempted before verification, the page shows the backend message and offers an inline resend action
- If verification token consumption fails because it is invalid or expired, the verification page offers resend recovery

## Backend Design

### Email Provider Contract

Resend is the only supported provider.

The backend keeps:

- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME`
- `EMAIL_VERIFY_EXPIRY_HOURS`
- `FRONTEND_URL`

The backend removes:

- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- any SMTP fallback behavior
- any email-provider code paths other than Resend

### Verification Email Sending

The current mail utility is replaced with a Resend-backed sender responsible for:

- constructing the frontend verification URL
- sending the verification message through Resend
- raising a real failure when Resend is not configured or the send fails

Production behavior must be honest:

- if registration or resend-verification cannot send the verification email, the backend should not pretend success
- no logged fallback links

### Auth Endpoints

`POST /auth/register`

- validates uniqueness
- creates the user and default stream
- creates a verification token
- sends the verification email through Resend
- returns success without logging the user in

`POST /auth/resend-verification`

- preserves generic outward response semantics for privacy
- if the user exists and is unverified, revokes prior verification tokens, creates a fresh token, and sends a Resend email
- if send fails for a real eligible user, the endpoint should fail rather than claiming the email was sent

`POST /auth/login`

- preserves existing verified-only login rule
- continues returning `Please verify your email before logging in.` for unverified users

`GET /auth/verify-email`

- preserves current token-consume semantics
- marks the user verified and revokes the token

### Backend Cleanup

The cleanup includes:

- config fields
- compose env wiring
- `.env.example`
- dependency list
- email utility implementation
- tests that currently assume SMTP behavior

## Frontend Design

### Route Truth

The frontend should treat auth as a server concern first.

`/`

- server-check session
- redirect authenticated users to `/journal`
- redirect unauthenticated users to `/login`

Dashboard route group

- add a server-side auth gate in `src/app/(dashboard)/layout.tsx`
- anonymous users are redirected before protected shell UI renders

Auth route group

- redirect authenticated users from `/login` and `/register` to `/journal`

### Session and Refresh Behavior

NextAuth remains the frontend session layer, but the visible app should only trust valid authenticated state.

- routes requiring auth should depend on server auth checks, not just client session hydration
- refresh failure should degrade directly to unauthenticated behavior
- logout should clear auth-sensitive React Query state immediately
- login success should invalidate or reset auth-sensitive caches before entering the app
- client queries should stay disabled unless status is authenticated and an access token exists

### Default Navigation

- post-login redirect target becomes `/journal`
- authenticated root redirect becomes `/journal`

This keeps landing behavior consistent.

## UX Flows

### Register

1. User submits registration form
2. Backend creates account and sends Resend verification email
3. Frontend shows success feedback
4. Frontend routes user to `/login`

### Login, Verified User

1. User submits email and password
2. Backend returns access token and refresh cookie
3. Frontend establishes session
4. Frontend refreshes auth-sensitive state
5. Frontend routes user to `/journal`

### Login, Unverified User

1. User submits email and password
2. Backend returns `Please verify your email before logging in.`
3. Login page shows that message
4. Login page offers inline resend-verification using the submitted email

### Verify Email

1. User lands on `/verify-email?token=...`
2. Frontend calls backend verification endpoint
3. On success, user is shown confirmation and routed to `/login`
4. On invalid or expired token, the page offers resend recovery

### Logout

1. Frontend signs out
2. Backend refresh token is revoked
3. Auth-sensitive query caches are cleared
4. User lands on `/login`

## Testing and Verification

### Backend

- register sends through Resend
- login remains blocked before verification
- resend-verification creates a fresh token and sends through Resend
- verify-email marks the user verified
- registration/resend fail honestly if the Resend send path fails
- no remaining runtime references to SMTP config

### Frontend

- auth config tests cover protected route rejection
- root redirect is auth-aware
- auth pages redirect authenticated users away
- dashboard layout redirects anonymous users before protected UI renders
- login page handles unverified responses and resend recovery
- logout clears auth-sensitive client state

## Implementation Notes

- Keep the backend token model unchanged
- Prefer small, explicit abstractions rather than a generic "notification provider" layer
- Remove dead config and dependencies in the same pass as behavior changes so deployment artifacts stay truthful
- Preserve privacy-safe outward behavior for resend-verification while still failing honestly for real delivery errors

## Risks

- Resend send failures will now block registration/resend instead of silently degrading; this is intentional and should be documented in env setup
- Tightening server-side auth gating may expose places that were accidentally relying on client-side rendering before session resolution
- Clearing query state on logout may reveal components that assumed cache persistence across sessions

## Success Criteria

- Only Resend-related email variables remain in backend runtime config
- Anonymous users cannot see protected dashboard shells at `/` or protected routes
- Authenticated users consistently land on `/journal`
- Unverified users can recover from the login screen by resending verification email
- Login, logout, and refresh flows do not leave stale user state behind
