from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domains.copy_trading.models import CopyWorkerHealth, WorkerHealthState


REQUIRED_WORKER_ROLES = (
    "telegram-session",
    "copy-signal",
    "copy-execution",
)


@dataclass(frozen=True)
class HealthSummary:
    status: str
    ready: bool
    components: list[dict]
    issues: list[str]


@dataclass(frozen=True)
class LaunchReadiness:
    ready: bool
    blockers: list[str]
    warnings: list[str]
    components: list[dict]
    stream_lag: int
    pending_events: int
    dead_letters: int
    oldest_uncertain_seconds: int
    global_paused: bool


def build_launch_readiness(
    health: HealthSummary,
    *,
    dead_letter_count: int,
    uncertain_intent_ages: list[int],
    uncertain_max_age_seconds: int,
    global_paused: bool,
) -> LaunchReadiness:
    blockers = []
    warnings = []
    if not health.ready:
        blockers.append("runtime_health")
    oldest_uncertain = max(uncertain_intent_ages or [0])
    if oldest_uncertain > uncertain_max_age_seconds:
        blockers.append("uncertain_intents")
    elif uncertain_intent_ages:
        warnings.append("broker_confirmation_pending")
    if dead_letter_count:
        blockers.append("dead_letters")
    if global_paused:
        warnings.append("global_pause_enabled")
    return LaunchReadiness(
        ready=not blockers,
        blockers=blockers,
        warnings=warnings,
        components=health.components,
        stream_lag=sum(item["stream_lag"] for item in health.components),
        pending_events=sum(item["pending_count"] for item in health.components),
        dead_letters=dead_letter_count,
        oldest_uncertain_seconds=oldest_uncertain,
        global_paused=global_paused,
    )


def aggregate_health(heartbeats, *, now: datetime | None = None, stale_after_seconds: int = 60) -> HealthSummary:
    now = now or datetime.now(timezone.utc)
    latest = {}
    for heartbeat in heartbeats:
        current = latest.get(heartbeat.worker_role)
        if current is None or heartbeat.heartbeat_at > current.heartbeat_at:
            latest[heartbeat.worker_role] = heartbeat
    components = []
    issues = []
    missing = False
    for role in REQUIRED_WORKER_ROLES:
        heartbeat = latest.get(role)
        if heartbeat is None:
            missing = True
            issues.append(f"{role} has not reported health.")
            components.append({"role": role, "status": "missing", "heartbeat_at": None, "stream_lag": 0, "pending_count": 0, "last_error": None})
            continue
        age = max(0, (now - heartbeat.heartbeat_at).total_seconds())
        raw_state = getattr(heartbeat.state, "value", heartbeat.state)
        status = "stale" if age > stale_after_seconds else str(raw_state)
        if status != "healthy":
            issues.append(f"{role} is {status}.")
        components.append({"role": role, "status": status, "heartbeat_at": heartbeat.heartbeat_at, "stream_lag": heartbeat.stream_lag, "pending_count": heartbeat.pending_count, "last_error": heartbeat.last_error})
    if missing:
        status = "action_required"
    elif issues:
        status = "degraded"
    else:
        status = "ready"
    return HealthSummary(status=status, ready=status == "ready", components=components, issues=issues)


def record_worker_health(*, worker_role: str, instance_id: str, stream_lag: int = 0, pending_count: int = 0, last_error: str | None = None) -> None:
    with SessionLocal() as db:
        item = db.execute(select(CopyWorkerHealth).where(CopyWorkerHealth.worker_role == worker_role, CopyWorkerHealth.instance_id == instance_id)).scalar_one_or_none()
        if item is None:
            item = CopyWorkerHealth(worker_role=worker_role, instance_id=instance_id, heartbeat_at=datetime.now(timezone.utc))
            db.add(item)
        item.heartbeat_at = datetime.now(timezone.utc)
        item.stream_lag = max(0, int(stream_lag))
        item.pending_count = max(0, int(pending_count))
        item.last_error = last_error
        item.state = WorkerHealthState.degraded if last_error else WorkerHealthState.healthy
        db.commit()
