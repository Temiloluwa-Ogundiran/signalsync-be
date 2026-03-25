from sqlalchemy import text
from sqlalchemy.orm import Session

# Stable lock key for journal sync cycle overlap prevention.
_JOURNAL_SYNC_CYCLE_LOCK_KEY = 91324051


def try_acquire_cycle_lock(db: Session) -> bool:
    stmt = text("SELECT pg_try_advisory_lock(:lock_key)")
    return bool(db.execute(stmt, {"lock_key": _JOURNAL_SYNC_CYCLE_LOCK_KEY}).scalar())


def release_cycle_lock(db: Session) -> None:
    stmt = text("SELECT pg_advisory_unlock(:lock_key)")
    db.execute(stmt, {"lock_key": _JOURNAL_SYNC_CYCLE_LOCK_KEY})
