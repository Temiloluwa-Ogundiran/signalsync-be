"""Email sending utilities backed by Resend."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("synctrades.email")


def _verification_link(raw_token: str) -> str:
    """Build the frontend verify-email URL embedded in the email."""
    return f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"


def _build_from_header() -> str:
    if not settings.EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is not configured.")
    if settings.EMAIL_FROM_NAME:
        return f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    return settings.EMAIL_FROM


def send_verification_email(to_email: str, raw_token: str) -> None:
    """Send an email verification link to the user through Resend."""
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")

    link = _verification_link(raw_token)
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;">
        <h2 style="color:#1a1a1a;">Verify your SyncTrades email</h2>
        <p style="color:#444;line-height:1.6;">
            Thanks for signing up! Click the button below to verify your email address.
            This link expires in <strong>{settings.EMAIL_VERIFY_EXPIRY_HOURS} hours</strong>.
        </p>
        <a href="{link}"
           style="display:inline-block;margin-top:16px;padding:12px 24px;
                  background:#2563eb;color:#fff;border-radius:6px;
                  text-decoration:none;font-weight:600;">
            Verify Email
        </a>
        <p style="margin-top:24px;font-size:13px;color:#888;">
            If you didn't create an account, you can safely ignore this email.
        </p>
        <hr style="margin-top:32px;border:none;border-top:1px solid #eee;">
        <p style="font-size:12px;color:#aaa;">
            Or copy this link into your browser:<br>
            <span style="word-break:break-all;">{link}</span>
        </p>
    </div>
    """

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
