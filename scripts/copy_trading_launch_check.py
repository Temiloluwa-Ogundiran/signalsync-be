import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.health import aggregate_health, build_launch_readiness
from app.domains.copy_trading.models import (
    CopiedTrade,
    CopyActivityEvent,
    CopyActivityLevel,
    CopyDeadLetter,
    CopyWorkerHealth,
    DeadLetterState,
    TradeIntent,
    TradeIntentState,
)


def migration_status(db) -> tuple[bool, dict]:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    expected = script.get_current_head()
    current = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return current == expected, {"current": current, "expected": expected}


def synthetic_status(db) -> tuple[bool, dict]:
    correlation_id = os.getenv("COPY_TRADING_SYNTHETIC_CORRELATION_ID", "").strip()
    if not correlation_id:
        return False, {"reason": "COPY_TRADING_SYNTHETIC_CORRELATION_ID is not set"}
    activities = db.execute(
        select(CopyActivityEvent).where(
            CopyActivityEvent.correlation_id == correlation_id
        )
    ).scalars().all()
    if not activities:
        return False, {"correlation_id": correlation_id, "activity_events": 0}
    route_ids = {event.route_id for event in activities if event.route_id}
    account_ids = {event.account_id for event in activities if event.account_id}
    first_seen = min(event.created_at for event in activities)
    intents = db.execute(
        select(TradeIntent).where(
            TradeIntent.route_id.in_(route_ids),
            TradeIntent.account_id.in_(account_ids),
            TradeIntent.created_at >= first_seen,
        )
    ).scalars().all()
    client_ids = {intent.client_order_id for intent in intents if intent.client_order_id}
    copied_count = db.execute(
        select(func.count(CopiedTrade.id)).where(
            CopiedTrade.intent_id.in_([intent.id for intent in intents])
        )
    ).scalar_one()
    okay = bool(activities) and len(client_ids) == 1 and copied_count == 1
    last_event = max(activities, key=lambda event: event.created_at)
    failures = [
        event
        for event in activities
        if event.level == CopyActivityLevel.error or event.action.endswith(".failed")
    ]
    last_failure = max(failures, key=lambda event: event.created_at) if failures else None
    result = {
        "correlation_id": correlation_id,
        "activity_events": len(activities),
        "client_order_ids": sorted(client_ids),
        "copied_trades": copied_count,
        "last_action": last_event.action,
        "last_title": last_event.title,
    }
    if last_failure is not None:
        result["failure_action"] = last_failure.action
        result["failure_title"] = last_failure.title
        result["failure_details"] = last_failure.parsed_details or {}
    return okay, result


def main() -> int:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        migration_ok, migrations = migration_status(db)
        health = aggregate_health(db.execute(select(CopyWorkerHealth)).scalars().all())
        dead_letters = db.execute(
            select(func.count(CopyDeadLetter.id)).where(
                CopyDeadLetter.state == DeadLetterState.pending
            )
        ).scalar_one()
        uncertain_times = db.execute(
            select(TradeIntent.created_at).where(
                TradeIntent.state.in_(
                    [TradeIntentState.uncertain, TradeIntentState.reconciling]
                )
            )
        ).scalars().all()
        readiness = build_launch_readiness(
            health,
            dead_letter_count=dead_letters,
            uncertain_intent_ages=[
                max(0, int((now - created_at).total_seconds()))
                for created_at in uncertain_times
            ],
            uncertain_max_age_seconds=settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS,
            global_paused=settings.COPY_TRADING_GLOBAL_PAUSED,
        )
        synthetic_ok, synthetic = synthetic_status(db)

    blockers = list(readiness.blockers)
    if not migration_ok:
        blockers.append("migrations")
    if not synthetic_ok:
        blockers.append("synthetic_flow")
    result = {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": readiness.warnings,
        "migrations": migrations,
        "workers": readiness.components,
        "dead_letters": readiness.dead_letters,
        "oldest_uncertain_seconds": readiness.oldest_uncertain_seconds,
        "synthetic": synthetic,
    }
    print(json.dumps(result, default=str, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
