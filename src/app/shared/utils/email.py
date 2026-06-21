"""Email sending utilities backed by Resend."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("synctrades.email")

# Brand
_BRAND = "#635BFF"  # TradePartna violet
_BRAND_DARK = "#4f46e5"
_INK = "#100A28"  # near-black wordmark ink
_MUTED = "#64748b"
_BORDER = "#e2e8f0"
_CANVAS = "#f4f5fb"


def _verification_link(raw_token: str) -> str:
    return f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"


def _reset_password_link(raw_token: str) -> str:
    return f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"


def _logo_url() -> str:
    return f"{settings.FRONTEND_URL}/brand/tradepartna-logo-full.png"


def _build_from_header() -> str:
    if not settings.EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is not configured.")
    if settings.EMAIL_FROM_NAME:
        return f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    return settings.EMAIL_FROM


def _render_email(
    *,
    heading: str,
    intro: str,
    button_label: str,
    button_url: str,
    expiry_note: str,
    footnote: str,
) -> str:
    """Branded, table-based transactional email shell (email-client safe).

    Uses inline styles + nested tables (no fl/grid) so it renders consistently
    in Gmail, Outlook, and Apple Mail. The CTA is a padded button, with a
    plain-text fallback link below it.
    """
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <title>{heading}</title>
</head>
<body style="margin:0;padding:0;background:{_CANVAS};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_CANVAS};padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;">
          <!-- Logo -->
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <img src="{_logo_url()}" alt="TradePartna" width="170"
                   style="display:block;width:170px;max-width:60%;height:auto;border:0;">
            </td>
          </tr>
          <!-- Card -->
          <tr>
            <td style="background:#ffffff;border:1px solid {_BORDER};border-radius:16px;padding:40px 36px;
                       box-shadow:0 1px 2px rgba(16,10,40,0.04),0 8px 24px -12px rgba(16,10,40,0.12);">
              <h1 style="margin:0 0 14px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                         font-size:22px;font-weight:700;line-height:1.3;color:{_INK};">
                {heading}
              </h1>
              <p style="margin:0 0 8px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                        font-size:15px;line-height:1.65;color:#334155;">
                {intro}
              </p>
              <p style="margin:0 0 28px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                        font-size:14px;line-height:1.6;color:{_MUTED};">
                {expiry_note}
              </p>
              <!-- Button -->
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" bgcolor="{_BRAND}"
                      style="border-radius:10px;background:linear-gradient(135deg,{_BRAND} 0%,{_BRAND_DARK} 100%);">
                    <a href="{button_url}"
                       style="display:inline-block;padding:14px 32px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                              font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">
                      {button_label}
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:28px 0 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                        font-size:13px;line-height:1.6;color:{_MUTED};">
                {footnote}
              </p>
              <hr style="margin:28px 0 16px;border:none;border-top:1px solid {_BORDER};">
              <p style="margin:0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                        font-size:12px;line-height:1.6;color:#94a3b8;">
                Button not working? Paste this link into your browser:<br>
                <a href="{button_url}" style="color:{_BRAND_DARK};word-break:break-all;">{button_url}</a>
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td align="center" style="padding:24px 8px 0;">
              <p style="margin:0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;
                        font-size:12px;line-height:1.6;color:#94a3b8;">
                &copy; TradePartna &middot; Your trading journal &amp; analytics
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _send(to_email: str, subject: str, html_body: str, *, context: str) -> None:
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": _build_from_header(),
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        },
        timeout=15,
    )
    if response.status_code >= 400:
        # Resend returns a JSON error body (e.g. unverified domain, recipient
        # not allowed in test mode). Surface it — raise_for_status alone hides it.
        logger.error(
            "Resend rejected %s email to %s: %s %s",
            context, to_email, response.status_code, response.text,
        )
    response.raise_for_status()
    logger.info("Resend accepted %s email to %s", context, to_email)


def send_verification_email(to_email: str, raw_token: str) -> None:
    """Send an email verification link to the user through Resend."""
    link = _verification_link(raw_token)
    html_body = _render_email(
        heading="Verify your email",
        intro="Welcome to TradePartna! Confirm your email address to activate your "
        "account and start journaling your trades.",
        button_label="Verify email",
        button_url=link,
        expiry_note=f"This link expires in {settings.EMAIL_VERIFY_EXPIRY_HOURS} hours.",
        footnote="If you didn't create an account, you can safely ignore this email.",
    )
    _send(to_email, "Verify your TradePartna email", html_body, context="verification")


def send_password_reset_email(to_email: str, raw_token: str) -> None:
    """Send a password reset link to the user through Resend."""
    link = _reset_password_link(raw_token)
    html_body = _render_email(
        heading="Reset your password",
        intro="We received a request to reset your TradePartna password. "
        "Click the button below to choose a new one.",
        button_label="Reset password",
        button_url=link,
        expiry_note=f"This link expires in {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes.",
        footnote="If you didn't request this, you can safely ignore this email — "
        "your password won't change.",
    )
    _send(to_email, "Reset your TradePartna password", html_body, context="password-reset")


_GUARD_TIER_COLOR = {
    "CAUTION": "#E3A008",
    "WARNING": "#F08C2E",
    "CRITICAL": "#F0555C",
    "PAUSED": "#F08C2E",
    "BREACHED": "#F0555C",
    "OFFLINE": "#64748b",
}


def send_guard_alert_email(to_email: str, *, tier: str, headline: str, detail: str,
                           guard_id: str) -> None:
    """A Partna Guard awareness alert. Read-only — we warn, we don't act.

    ``tier`` is the standing crossed (CAUTION/WARNING/CRITICAL/BREACHED/OFFLINE).
    """
    import html

    color = _GUARD_TIER_COLOR.get(tier, _BRAND)
    link = f"{settings.FRONTEND_URL.rstrip('/')}/guard"
    body = _render_email(
        heading=html.escape(headline),
        intro=html.escape(detail),
        button_label="View Partna Guard",
        button_url=link,
        expiry_note=(
            "Partna Guard is read-only — it warns, it does not close your trades. "
            "It reduces breach risk but cannot guarantee passing or prevent all "
            "losses; outages, gaps and news spikes are disclaimed."
        ),
        footnote="You're receiving this because Guard alerts are enabled on this account.",
    )
    # Tint the heading rule with the tier color via a leading marker the shell keeps.
    body = body.replace(_BRAND, color) if tier in _GUARD_TIER_COLOR else body
    _send(to_email, f"Partna Guard · {html.escape(headline)}", body, context="guard")


def send_copy_trading_email(to_email: str, *, subject: str, details: dict) -> None:
    import html
    rows = "".join(
        f"<tr><td style='padding:8px;border-bottom:1px solid {_BORDER};color:{_MUTED}'>{html.escape(str(key).replace('_', ' ').title())}</td>"
        f"<td style='padding:8px;border-bottom:1px solid {_BORDER};color:{_INK}'>{html.escape(str(value))}</td></tr>"
        for key, value in details.items()
        if value not in (None, "", [], {})
    )
    body = f"<html><body style='font-family:Segoe UI,Arial;background:{_CANVAS};padding:24px'><div style='max-width:560px;margin:auto;background:white;border:1px solid {_BORDER};border-radius:12px;padding:24px'><h2 style='color:{_INK}'>{html.escape(subject)}</h2><table style='width:100%;border-collapse:collapse'>{rows}</table></div></body></html>"
    _send(to_email, subject, body, context="copy-trading")
