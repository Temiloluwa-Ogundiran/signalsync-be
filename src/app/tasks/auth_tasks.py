import logging
from datetime import datetime, timedelta, timezone

from app.core.celery_app import celery_app
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_PURGE_OLDER_THAN_DAYS = 30


@celery_app.task(
    name="auth.send_verification_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def send_verification_email_task(self, to_email: str, raw_token: str) -> None:
    from app.shared.utils.email import send_verification_email  # noqa: PLC0415
    send_verification_email(to_email, raw_token)


@celery_app.task(
    name="auth.send_password_reset_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def send_password_reset_email_task(self, to_email: str, raw_token: str) -> None:
    from app.shared.utils.email import send_password_reset_email  # noqa: PLC0415
    send_password_reset_email(to_email, raw_token)


@celery_app.task(name="auth.purge_expired_tokens")
def purge_expired_tokens() -> dict:
    """
    Delete tokens that expired more than 30 days ago.

    Expired tokens are functionally dead — they cannot be used for auth (JWT exp
    is always checked at decode time) and the partial index on
    (expires_at WHERE is_revoked = false) means they no longer affect query
    performance after expiry.  This task reclaims table space over time.

    Wire up in celery_app.py beat_schedule to run daily, e.g.:
        celery_app.conf.beat_schedule = {
            "purge-expired-tokens-daily": {
                "task": "auth.purge_expired_tokens",
                "schedule": crontab(hour=3, minute=0),
            },
        }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_PURGE_OLDER_THAN_DAYS)

    with SessionLocal() as db:
        from sqlalchemy import delete  # noqa: PLC0415
        from app.domains.auth.models import Token  # noqa: PLC0415

        result = db.execute(
            delete(Token).where(Token.expires_at < cutoff)
        )
        db.commit()

    deleted = result.rowcount
    logger.info("purge_expired_tokens: deleted %d token(s) older than %s", deleted, cutoff.date())
    return {"deleted": deleted, "cutoff": cutoff.isoformat()}
