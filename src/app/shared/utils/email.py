"""
Email sending utilities using SMTP via fastapi-mail.
"""

import asyncio
import logging

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import settings

logger = logging.getLogger("synctrades.email")


def _get_mail_config() -> ConnectionConfig:
    """Build fastapi-mail connection config from settings."""
    return ConnectionConfig(
        MAIL_USERNAME=settings.SMTP_USERNAME or "",
        MAIL_PASSWORD=settings.SMTP_PASSWORD or "",
        MAIL_FROM=settings.EMAIL_FROM or "noreply@synctrades.com",
        MAIL_PORT=settings.SMTP_PORT,
        MAIL_SERVER=settings.SMTP_SERVER or "localhost",
        MAIL_FROM_NAME=settings.EMAIL_FROM_NAME,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        USE_CREDENTIALS=bool(settings.SMTP_USERNAME and settings.SMTP_PASSWORD),
        VALIDATE_CERTS=True,
    )


def _verification_link(raw_token: str) -> str:
    """Build the frontend verify-email URL embedded in the email."""
    return f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"


def send_verification_email(to_email: str, raw_token: str) -> None:
    """
    Send an email verification link to the user.

    Runs the async fastapi-mail send call synchronously so it can be
    called from regular (non-async) service functions.
    If SMTP is not configured the link is logged to stdout as a fallback.
    """
    if not settings.SMTP_SERVER:
        logger.warning(
            "[EMAIL FALLBACK] SMTP not configured. Verification link for %s → %s",
            to_email,
            _verification_link(raw_token),
        )
        return

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

    message = MessageSchema(
        subject="Verify your SyncTrades email",
        recipients=[to_email],
        body=html_body,
        subtype=MessageType.html,
    )

    async def _send() -> None:
        fm = FastMail(_get_mail_config())
        await fm.send_message(message)

    asyncio.run(_send())
