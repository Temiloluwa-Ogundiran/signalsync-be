# Auth Resend and Session Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Resend the only backend email provider, enforce protected frontend routing from server truth, and finish the signup, verification, signin, refresh, and logout flows without stale auth state.

**Architecture:** Keep the backend auth token model as-is and replace only the email delivery seam: `auth.service` continues issuing verification tokens, while `shared/utils/email.py` becomes a single-purpose Resend sender backed by `httpx`. On the frontend, move route protection to server components and make client auth flows explicitly reset React Query state so protected UI never paints for anonymous users and stale session data cannot linger across signin/signout transitions.

**Tech Stack:** FastAPI, SQLAlchemy, `httpx`, Pydantic settings, Next.js App Router, NextAuth v5 beta, React Query, Node `node:test`, pytest

---

## File Structure

### Backend files

- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/core/config.py`
  - Remove SMTP settings and add the Resend-only env contract.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/shared/utils/email.py`
  - Replace `fastapi-mail` SMTP sending with a small Resend API client using `httpx`.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/auth/service.py`
  - Preserve auth token behavior while making registration and resend fail honestly when delivery fails.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/pyproject.toml`
  - Remove the now-dead `fastapi-mail` dependency.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`
  - Keep only Resend-related email variables.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`
  - Swap SMTP env injection for Resend env injection.
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_auth_email_and_verification.py`
  - Focused backend regression coverage for registration, resend, verify, and login-before-verify behavior.

### Frontend files

- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/page.tsx`
  - Make `/` auth-aware on the server.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(dashboard)/layout.tsx`
  - Convert to a server-side auth gate that wraps a client dashboard shell.
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(dashboard)/dashboard-shell.tsx`
  - Hold the existing client-only dashboard chrome and modal tree.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/login/page.tsx`
  - Redirect authenticated users to `/journal` and pass server search params into the login form.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/register/page.tsx`
  - Redirect authenticated users to `/journal`.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/verify-email/page.tsx`
  - Add resend recovery UX for invalid or expired tokens.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.ts`
  - Tighten protected-route detection, post-auth redirects, refresh failure handling, and backend logout event behavior.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.ts`
  - Keep credentials auth aligned with backend responses and ensure post-login route target becomes `/journal`.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/providers.tsx`
  - Clear or invalidate auth-sensitive query state on signin/signout transitions.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/api/auth.api.ts`
  - Add `resendVerificationEmail`.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/login-form.tsx`
  - Render unverified-email recovery state and reset caches before entering the app.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/register-form.tsx`
  - Route to `/login` with verification context after successful registration.
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/resend-verification-form.tsx`
  - Shared resend UX for login and verify-email flows.
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.ts`
  - Small helpers for clearing or invalidating auth-sensitive queries.
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.test.ts`
  - Node `node:test` coverage for the query-state helpers.
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts`
  - Extend auth callback coverage for protected routes and auth page redirects.

## Task 1: Replace SMTP With a Resend-Only Backend Sender

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/core/config.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/shared/utils/email.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/pyproject.toml`
- Test: `C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_auth_email_and_verification.py`

- [ ] **Step 1: Write the failing backend sender tests**

```python
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.shared.utils.email import send_verification_email
from app.domains.auth import service as auth_service
from app.domains.auth.schemas import ResendVerificationRequest


def test_send_verification_email_posts_resend_payload() -> None:
    with patch("app.shared.utils.email.httpx.post") as post_mock, patch(
        "app.shared.utils.email.settings"
    ) as settings_mock:
        settings_mock.RESEND_API_KEY = "re_test"
        settings_mock.EMAIL_FROM = "hello@synctrades.com"
        settings_mock.EMAIL_FROM_NAME = "SyncTrades"
        settings_mock.FRONTEND_URL = "https://app.synctrades.com"
        settings_mock.EMAIL_VERIFY_EXPIRY_HOURS = 24
        post_mock.return_value.raise_for_status.return_value = None

        send_verification_email("user@example.com", "token-123")

    post_mock.assert_called_once()
    payload = post_mock.call_args.kwargs["json"]
    assert payload["to"] == ["user@example.com"]
    assert payload["from"] == "SyncTrades <hello@synctrades.com>"
    assert "verify-email?token=token-123" in payload["html"]


def test_send_verification_email_raises_when_resend_is_unconfigured() -> None:
    with patch("app.shared.utils.email.settings") as settings_mock:
        settings_mock.RESEND_API_KEY = ""
        settings_mock.EMAIL_FROM = ""

        with pytest.raises(RuntimeError):
            send_verification_email("user@example.com", "token-123")
```

- [ ] **Step 2: Run the sender tests to verify they fail**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest tests/test_auth_email_and_verification.py -q
```

Expected: FAIL because `settings.RESEND_API_KEY` does not exist yet and the current sender still imports `fastapi_mail`.

- [ ] **Step 3: Implement the Resend-only config and sender**

`config.py` target shape:

```python
class Settings(BaseSettings):
    ...
    EMAIL_VERIFY_EXPIRY_HOURS: int = 24
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "SyncTrades"
```

`email.py` target shape:

```python
import logging
import httpx

from app.core.config import settings

logger = logging.getLogger("synctrades.email")


def _verification_link(raw_token: str) -> str:
    return f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"


def _build_from_header() -> str:
    if not settings.EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is not configured.")
    if settings.EMAIL_FROM_NAME:
        return f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    return settings.EMAIL_FROM


def send_verification_email(to_email: str, raw_token: str) -> None:
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")

    link = _verification_link(raw_token)
    html_body = f\"\"\"
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;">
      <h2 style="color:#1a1a1a;">Verify your SyncTrades email</h2>
      <p style="color:#444;line-height:1.6;">
        Thanks for signing up! Click the button below to verify your email address.
        This link expires in <strong>{settings.EMAIL_VERIFY_EXPIRY_HOURS} hours</strong>.
      </p>
      <a href="{link}" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#2563eb;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;">
        Verify Email
      </a>
    </div>
    \"\"\"

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": _build_from_header(),
            "to": [to_email],
            "subject": "Verify your SyncTrades email",
            "html": html_body,
        },
        timeout=15,
    )
    response.raise_for_status()
```

`pyproject.toml` dependency cleanup:

```toml
dependencies = [
    "alembic>=1.18.4",
    "bcrypt==4.0.1",
    "celery[redis]>=5.5.3",
    "cryptography>=45.0.7",
    "fastapi[standard]>=0.135.1",
    "httpx>=0.27.0",
    ...
]
```

- [ ] **Step 4: Re-run the sender tests**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest tests/test_auth_email_and_verification.py -q
```

Expected: PASS for the two Resend sender tests, with no `fastapi_mail` import errors.

- [ ] **Step 5: Commit**

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-be add src/app/core/config.py src/app/shared/utils/email.py pyproject.toml tests/test_auth_email_and_verification.py
git -C C:/Users/USER/Documents/SyncTrade/synctrades-be commit -m "refactor: replace smtp auth email with resend"
```

## Task 2: Make Backend Auth Flows Fail Honestly and Clean Up Runtime Env

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/auth/service.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`
- Test: `C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_auth_email_and_verification.py`

- [ ] **Step 1: Add failing auth flow tests**

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException, Response

import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.auth.schemas import RegisterRequest, ResendVerificationRequest
from app.domains.auth import service as auth_service


def test_register_raises_when_email_delivery_fails() -> None:
    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.display_name = "Trader"
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=None),
        patch("app.domains.auth.service.user_repo.get_by_username", return_value=None),
        patch("app.domains.auth.service.user_repo.create", return_value=user),
        patch("app.domains.auth.service.stream_repo.create"),
        patch("app.domains.auth.service.token_repo.create"),
        patch("app.domains.auth.service.send_verification_email", side_effect=RuntimeError("resend down")),
    ):
        with pytest.raises(RuntimeError, match="resend down"):
            auth_service.register(
                db,
                RegisterRequest(
                    email="user@example.com",
                    username="trader",
                    display_name="Trader",
                    password="password123",
                ),
            )


def test_resend_verification_raises_for_real_delivery_failure() -> None:
    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_email_verified = False
    db.query.return_value.filter_by.return_value.all.return_value = []
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=user),
        patch("app.domains.auth.service.token_repo.create"),
        patch("app.domains.auth.service.send_verification_email", side_effect=RuntimeError("resend down")),
    ):
        with pytest.raises(RuntimeError, match="resend down"):
            auth_service.resend_verification(
                db, ResendVerificationRequest(email="user@example.com")
            )


def test_login_still_blocks_unverified_user() -> None:
    db = MagicMock()
    response = Response()
    user = MagicMock()
    user.hashed_password = "hashed"
    user.is_email_verified = False
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=user),
        patch("app.domains.auth.service.verify_password", return_value=True),
    ):
        with pytest.raises(HTTPException) as exc:
            auth_service.login(db, "user@example.com", "password123", response)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Please verify your email before logging in."
```

- [ ] **Step 2: Run the backend auth tests to verify they fail**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest tests/test_auth_email_and_verification.py -q
```

Expected: FAIL because `register()` and `resend_verification()` do not yet enforce honest send failures, and env files still expose SMTP variables.

- [ ] **Step 3: Update auth service and runtime env files**

`service.py` target behavior:

```python
def register(db: Session, payload: RegisterRequest) -> RegisterResponse:
    ...
    db.commit()
    db.refresh(user)
    send_verification_email(user.email, raw_token)
    return RegisterResponse(
        message="Account created. Please check your email to verify your address.",
        user=UserResponse.model_validate(user),
    )


def resend_verification(
    db: Session, payload: ResendVerificationRequest
) -> ResendVerificationResponse:
    user = user_repo.get_by_email(db, payload.email)
    if not user or user.is_email_verified:
        return ResendVerificationResponse(
            message="If that email is registered and unverified, a new link has been sent."
        )
    ...
    db.commit()
    send_verification_email(user.email, raw_token)
    return ResendVerificationResponse(
        message="If that email is registered and unverified, a new link has been sent."
    )
```

`.env.example` email section target:

```env
EMAIL_VERIFY_EXPIRY_HOURS=24
RESEND_API_KEY=
EMAIL_FROM=
EMAIL_FROM_NAME=SyncTrades
```

`docker-compose.yml` target env block:

```yaml
      EMAIL_VERIFY_EXPIRY_HOURS: ${EMAIL_VERIFY_EXPIRY_HOURS:-24}
      RESEND_API_KEY: ${RESEND_API_KEY:-}
      EMAIL_FROM: ${EMAIL_FROM:-}
      EMAIL_FROM_NAME: ${EMAIL_FROM_NAME:-SyncTrades}
```

Remove all `SMTP_*` lines from both files.

- [ ] **Step 4: Run backend auth verification and env scans**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest tests/test_auth_email_and_verification.py -q
rg -n "SMTP_|fastapi-mail|fastapi_mail" C:/Users/USER/Documents/SyncTrade/synctrades-be
```

Expected:
- pytest PASS
- `rg` returns no matches under runtime code, `.env.example`, compose, or dependency declarations

- [ ] **Step 5: Commit**

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-be add src/app/domains/auth/service.py .env.example docker-compose.yml tests/test_auth_email_and_verification.py
git -C C:/Users/USER/Documents/SyncTrade/synctrades-be commit -m "fix: harden auth verification delivery flow"
```

## Task 3: Enforce Auth From the Server Before Dashboard UI Can Render

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/page.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(dashboard)/layout.tsx`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(dashboard)/dashboard-shell.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/login/page.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/register/page.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.ts`
- Test: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts`

- [ ] **Step 1: Extend the auth callback tests**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { authConfig } from "./auth.config.ts";

test("authorized callback redirects authenticated users away from login", () => {
  const result = authConfig.callbacks.authorized({
    auth: {
      user: { id: "user-1" },
      accessToken: "access-token",
    } as never,
    request: {
      nextUrl: new URL("http://localhost:3000/login"),
    } as never,
  });

  assert.ok(result instanceof Response);
  assert.equal(result.headers.get("location"), "http://localhost:3000/journal");
});

test("authorized callback rejects anonymous journal access", () => {
  const result = authConfig.callbacks.authorized({
    auth: null,
    request: {
      nextUrl: new URL("http://localhost:3000/journal"),
    } as never,
  });

  assert.equal(result, false);
});
```

- [ ] **Step 2: Run the frontend auth tests to verify they fail**

Run:

```powershell
node --experimental-strip-types --test C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts
```

Expected: FAIL because the redirect target is currently `/overview`, and root/dashboard gating is still not server-owned.

- [ ] **Step 3: Implement server-side route truth**

`src/app/page.tsx` target:

```tsx
import { redirect } from "next/navigation";
import { auth } from "../auth";

export default async function HomePage() {
  const session = await auth();
  redirect(session?.accessToken ? "/journal" : "/login");
}
```

`src/app/(dashboard)/layout.tsx` target:

```tsx
import { redirect } from "next/navigation";
import { auth } from "../../../auth";
import DashboardShell from "./dashboard-shell";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  if (!session?.accessToken || session.error === "RefreshAccessTokenError") {
    redirect("/login");
  }

  return <DashboardShell>{children}</DashboardShell>;
}
```

`src/app/(dashboard)/dashboard-shell.tsx` should contain the current client layout body moved over unchanged except for the default export name.

`src/app/(auth)/login/page.tsx` and `register/page.tsx` target pattern:

```tsx
import { redirect } from "next/navigation";
import { auth } from "../../../../auth";

export default async function LoginPage() {
  const session = await auth();
  if (session?.accessToken) {
    redirect("/journal");
  }
  ...
}
```

`auth.config.ts` redirect target change:

```ts
if (isLoggedIn && isAuthRoute) {
  return Response.redirect(new URL("/journal", nextUrl));
}
```

- [ ] **Step 4: Run callback tests and a production build**

Run:

```powershell
node --experimental-strip-types --test C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts
yarn --cwd C:/Users/USER/Documents/SyncTrade/synctrades-fe build
```

Expected:
- auth callback tests PASS
- Next build PASS with the dashboard shell split and server route guards in place

- [ ] **Step 5: Commit**

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe add src/app/page.tsx src/app/(dashboard)/layout.tsx src/app/(dashboard)/dashboard-shell.tsx src/app/(auth)/login/page.tsx src/app/(auth)/register/page.tsx src/auth.config.ts src/auth.config.test.ts
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe commit -m "fix: enforce server auth boundaries"
```

## Task 4: Finish Login, Resend Verification, Verify-Email Recovery, and Cache Reset

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/api/auth.api.ts`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/login-form.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/register-form.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/(auth)/verify-email/page.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/app/providers.tsx`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/components/sidebar.tsx`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/components/resend-verification-form.tsx`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.ts`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.test.ts`

- [ ] **Step 1: Write the failing frontend helper tests**

`auth-query-state.test.ts`:

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { resetAuthSensitiveQueries } from "./auth-query-state.ts";

test("resetAuthSensitiveQueries clears auth-sensitive query keys", () => {
  const calls: Array<unknown> = [];
  const queryClient = {
    removeQueries: (args: unknown) => calls.push(args),
  } as const;

  resetAuthSensitiveQueries(queryClient as never);

  assert.deepEqual(calls, [
    { queryKey: ["journal-accounts"] },
    { queryKey: ["journal-analytics"] },
    { queryKey: ["journal-trade-history"] },
    { queryKey: ["journal-day"] },
    { queryKey: ["my-streams"] },
    { queryKey: ["discover-streams"] },
    { queryKey: ["stream-detail"] },
    { queryKey: ["my-posts"] },
  ]);
});
```

- [ ] **Step 2: Run the helper tests to verify they fail**

Run:

```powershell
node --experimental-strip-types --test C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.test.ts
```

Expected: FAIL because the helper file does not exist yet.

- [ ] **Step 3: Implement the frontend auth flow cleanup**

`auth.api.ts` target addition:

```ts
export interface ResendVerificationResponse {
  message: string;
}

export async function resendVerificationEmail(
  email: string,
): Promise<ResendVerificationResponse> {
  const res = await apiClient.post<ResendVerificationResponse>(
    "/auth/resend-verification",
    { email },
  );
  return res.data;
}
```

`auth-query-state.ts` target:

```ts
import type { QueryClient } from "@tanstack/react-query";

const AUTH_QUERY_KEYS = [
  ["journal-accounts"],
  ["journal-analytics"],
  ["journal-trade-history"],
  ["journal-day"],
  ["my-streams"],
  ["discover-streams"],
  ["stream-detail"],
  ["my-posts"],
] as const;

export function resetAuthSensitiveQueries(queryClient: QueryClient) {
  for (const queryKey of AUTH_QUERY_KEYS) {
    queryClient.removeQueries({ queryKey: [...queryKey] });
  }
}

export function refreshAuthSensitiveQueries(queryClient: QueryClient) {
  for (const queryKey of AUTH_QUERY_KEYS) {
    queryClient.invalidateQueries({ queryKey: [...queryKey] });
  }
}
```

`login-form.tsx` target changes:

```tsx
const UNVERIFIED_MESSAGE = "Please verify your email before logging in.";

export function LoginForm({
  initialEmail = "",
  justRegistered = false,
}: {
  initialEmail?: string;
  justRegistered?: boolean;
}) {
  const queryClient = useQueryClient();
  const [showResend, setShowResend] = useState(false);
  ...
  const result = await signIn("credentials", {
    email: values.email,
    password: values.password,
    redirect: false,
  });

  if (result?.error) {
    const message = getAuthErrorMessage(result.error, result.code);
    setShowResend(message === UNVERIFIED_MESSAGE);
    form.setError("root", { message });
    return;
  }

  refreshAuthSensitiveQueries(queryClient);
  router.replace("/journal");
  router.refresh();
```

Render the shared resend form when `showResend` is true, seeded with the current email field value.

`register-form.tsx` redirect target:

```tsx
router.push(`/login?registered=1&email=${encodeURIComponent(values.email)}`);
```

`verify-email/page.tsx` target recovery block:

```tsx
{error ? (
  <div className="space-y-4">
    <p className="text-sm text-muted-foreground">{error}</p>
    <ResendVerificationForm />
  </div>
) : (
  ...
)}
```

`providers.tsx` target session sync:

```tsx
function SessionQuerySync() {
  const { data: session, status } = useSession();
  const queryClient = useQueryClient();
  const lastAuthStateRef = useRef<"authenticated" | "anonymous">("anonymous");

  useEffect(() => {
    const isAuthenticated =
      status === "authenticated" &&
      !!session?.accessToken &&
      session.error !== "RefreshAccessTokenError";

    const nextState = isAuthenticated ? "authenticated" : "anonymous";
    if (nextState !== lastAuthStateRef.current) {
      if (nextState === "anonymous") {
        resetAuthSensitiveQueries(queryClient);
      } else {
        refreshAuthSensitiveQueries(queryClient);
      }
      lastAuthStateRef.current = nextState;
    }
  }, [status, session?.accessToken, session?.error, queryClient]);
}
```

`sidebar.tsx` logout click target:

```tsx
onClick={() => signOut({ callbackUrl: "/login", redirect: true })}
```

- [ ] **Step 4: Run tests, lint, and build**

Run:

```powershell
node --experimental-strip-types --test C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.test.ts
yarn --cwd C:/Users/USER/Documents/SyncTrade/synctrades-fe eslint "src/features/auth/api/auth.api.ts" "src/features/auth/components/login-form.tsx" "src/features/auth/components/register-form.tsx" "src/features/auth/components/resend-verification-form.tsx" "src/features/auth/lib/auth-query-state.ts" "src/app/providers.tsx" "src/app/(auth)/verify-email/page.tsx" "src/components/sidebar.tsx"
yarn --cwd C:/Users/USER/Documents/SyncTrade/synctrades-fe build
```

Expected:
- both `node:test` suites PASS
- eslint returns `0 errors`
- build PASS

- [ ] **Step 5: Commit**

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe add src/features/auth/api/auth.api.ts src/features/auth/components/login-form.tsx src/features/auth/components/register-form.tsx src/features/auth/components/resend-verification-form.tsx src/features/auth/lib/auth-query-state.ts src/features/auth/lib/auth-query-state.test.ts src/app/(auth)/verify-email/page.tsx src/app/providers.tsx src/components/sidebar.tsx
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe commit -m "fix: complete auth verification and cache reset flow"
```

## Task 5: Final Cross-Repo Regression Sweep

**Files:**
- Verify only; no new source files

- [ ] **Step 1: Run the backend auth test slice**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_auth_email_and_verification.py -q
```

Expected: PASS

- [ ] **Step 2: Run the broader backend suite for collateral damage**

Run:

```powershell
uv run --with pytest --with pytest-httpx python -m pytest C:/Users/USER/Documents/SyncTrade/synctrades-be/tests -q
```

Expected: PASS, or only pre-existing unrelated failures if discovered. If failures are unrelated and pre-existing, stop and document them before proceeding.

- [ ] **Step 3: Run the frontend auth tests and build again**

Run:

```powershell
node --experimental-strip-types --test C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/auth.config.test.ts C:/Users/USER/Documents/SyncTrade/synctrades-fe/src/features/auth/lib/auth-query-state.test.ts
yarn --cwd C:/Users/USER/Documents/SyncTrade/synctrades-fe build
```

Expected: PASS

- [ ] **Step 4: Run final dead-config scans**

Run:

```powershell
rg -n "SMTP_|fastapi-mail|fastapi_mail" C:/Users/USER/Documents/SyncTrade/synctrades-be
rg -n "/overview|redirect\\(\"/overview|Response.redirect\\(new URL\\(\"/overview" C:/Users/USER/Documents/SyncTrade/synctrades-fe/src
```

Expected:
- no SMTP-related runtime matches in backend
- no lingering auth redirects to `/overview` in the frontend auth flow

- [ ] **Step 5: Commit final cleanup if needed**

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-be status --short
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe status --short
```

Expected: clean working trees after the prior task commits. If any final cleanup edits were needed during the regression sweep, commit them with one focused follow-up message such as:

```powershell
git -C C:/Users/USER/Documents/SyncTrade/synctrades-fe commit -m "chore: finalize auth flow cleanup"
```

## Self-Review

- Spec coverage:
  - Resend-only backend provider: Task 1 and Task 2
  - Remove SMTP and dead env wiring: Task 1, Task 2, Task 5
  - Root redirect and server-side dashboard gating: Task 3
  - Auth-page redirects for authenticated users: Task 3
  - Signup -> verify -> signin -> `/journal`: Task 3 and Task 4
  - Inline resend-verification recovery: Task 4
  - Stale session/query cleanup on signin/signout/refresh failure: Task 4
  - Regression verification: Task 5
- Placeholder scan:
  - No `TODO`, `TBD`, or “similar to above” markers remain.
- Type consistency:
  - `resetAuthSensitiveQueries`, `refreshAuthSensitiveQueries`, and `resendVerificationEmail` are named consistently across the plan.
