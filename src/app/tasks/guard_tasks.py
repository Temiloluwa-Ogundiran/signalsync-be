"""Celery tasks for Partna Guard — the always-on watcher + alert emails.

RULES.md EXCEPTION: Guard polls on a schedule (Celery beat fans out one poll per
enabled account every N seconds). This is the only scheduled-sync path in the app
and it is intentional — a watchdog that only checks on demand is useless. Do not
convert this to on-demand.
"""

import logging
import uuid

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.domains.guard import repository as guard_repo
from app.domains.guard import watcher
from app.domains.users.models import User

logger = logging.getLogger(__name__)


@celery_app.task(name="guard.enqueue_polls", bind=True, max_retries=0)
def enqueue_polls(self) -> dict:
    """Beat entry: fan out one poll task per enabled guard account."""
    _ = self
    with SessionLocal() as db:
        ids = guard_repo.list_enabled_guard_account_ids(db)
    for gid in ids:
        poll_account.delay(str(gid))
    return {"enqueued": len(ids)}


@celery_app.task(name="guard.poll_account", bind=True, max_retries=0)
def poll_account(self, guard_account_id: str) -> dict:
    """Run a single poll of one account. Owns its transaction."""
    _ = self
    with SessionLocal() as db:
        guard = guard_repo.get_guard_account(db, uuid.UUID(guard_account_id))
        if guard is None or not guard.enabled:
            return {"status": "skipped"}
        status = watcher.run_one_poll(db, guard)
        db.commit()
        return {"status": status or "offline"}


@celery_app.task(
    name="guard.send_alert_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def send_guard_alert_email_task(self, guard_account_id: str, tier: str,
                                headline: str, detail: str, alert_id: str) -> None:
    from app.shared.utils.email import send_guard_alert_email

    with SessionLocal() as db:
        guard = guard_repo.get_guard_account(db, uuid.UUID(guard_account_id))
        if guard is None:
            return
        user = db.get(User, guard.user_id)
        if user is None or not user.email:
            return
        to_email = user.email
        gid = str(guard.id)

    try:
        send_guard_alert_email(to_email, tier=tier, headline=headline,
                               detail=detail, guard_id=gid)
        # Mark the alert row sent_ok.
        with SessionLocal() as db:
            alert = db.get(_alert_model(), uuid.UUID(alert_id))
            if alert is not None:
                alert.sent_ok = True
                db.commit()
        logger.info("guard alert email sent to %s (tier=%s, attempt=%s)",
                    to_email, tier, self.request.retries)
    except Exception:
        logger.exception("guard alert email FAILED to %s (tier=%s, attempt=%s/%s)",
                         to_email, tier, self.request.retries, self.max_retries)
        raise


def _alert_model():
    from app.domains.guard.models import GuardAlert
    return GuardAlert
