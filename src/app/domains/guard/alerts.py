"""Guard alert dispatch — email on tier ESCALATION only, de-duped.

Mirror the prototype's escalation guard: keep the last standing per account, fire
only when the tier climbs (HEALTHY → CAUTION → WARNING → CRITICAL → BREACHED), never
on de-escalation or repeats. Each fire writes a guard_alerts row and queues a Resend
email via Celery. v1 channel = email only.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from . import repository as guard_repo
from .models import GuardAccount
from .nudges import nudge_for
from .state import AccountState

logger = logging.getLogger(__name__)

# The standing ladder. BREACHED is a firm-line breach; otherwise we map the engine
# Status tiers (HEALTHY/CAUTION/WARNING/CRITICAL) straight across.
_LADDER = ["HEALTHY", "CAUTION", "WARNING", "CRITICAL", "BREACHED"]


def _standing(state: AccountState) -> str:
    if state.breached:
        return "BREACHED"
    return state.status.value if state.status.value in _LADDER else "HEALTHY"


def _escalated(prev: Optional[str], current: str) -> bool:
    """True only when current is strictly higher on the ladder than prev."""
    if current == "HEALTHY":
        return False
    p = _LADDER.index(prev) if prev in _LADDER else 0
    c = _LADDER.index(current)
    return c > p


def maybe_alert(db: Session, guard: GuardAccount, state: AccountState) -> None:
    """Fire an email iff the standing escalated since the last alert. No commit."""
    current = _standing(state)
    prev = guard.last_alert_tier
    if not _escalated(prev, current):
        # Still record the latest tier so a later climb compares correctly, but only
        # downward moves update the baseline (so we re-alert if it climbs again).
        if current == "HEALTHY":
            guard.last_alert_tier = "HEALTHY"
        return

    guard.last_alert_tier = current
    headline, detail = _copy(current, state)
    alert = guard_repo.record_alert(db, guard_id=guard.id, tier=current, kind="threshold")
    _queue_email(guard, current, headline, detail, alert_id=str(alert.id))


def alert_offline(db: Session, guard: GuardAccount) -> None:
    """Monitoring went offline — the worst moment to be blind. Email once."""
    headline = "Monitoring offline"
    detail = ("Partna Guard lost its connection to your account and is retrying. "
              "If you have open risk, manage it manually until monitoring resumes.")
    alert = guard_repo.record_alert(db, guard_id=guard.id, tier="OFFLINE", kind="offline")
    _queue_email(guard, "OFFLINE", headline, detail, alert_id=str(alert.id))


def _copy(tier: str, state: AccountState) -> tuple[str, str]:
    nudge = nudge_for(state)
    if tier == "BREACHED":
        return "Limit breached", nudge
    if tier == "CRITICAL":
        return "On the edge", nudge
    if tier == "WARNING":
        return "Close to the line", nudge
    return "Tightening up", nudge


def _queue_email(guard: GuardAccount, tier: str, headline: str, detail: str,
                 *, alert_id: str) -> None:
    """Queue the Resend email via Celery (best-effort; never blocks the poll)."""
    try:
        from app.tasks.guard_tasks import send_guard_alert_email_task
        send_guard_alert_email_task.delay(str(guard.id), tier, headline, detail, alert_id)
    except Exception:  # noqa: BLE001 - queuing must never break the watcher
        logger.exception("failed to queue guard alert email for %s", guard.id)
