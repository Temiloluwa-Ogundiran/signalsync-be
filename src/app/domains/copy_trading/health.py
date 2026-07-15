from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domains.copy_trading.models import CopyWorkerHealth, WorkerHealthState


REQUIRED_WORKER_ROLES = (
    "telegram-session",
    "copy-signal",
    "copy-execution",
    "copy-provisioning",
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
    oldest_active_intent_seconds: int
    global_paused: bool


def add_metaapi_health(
    health: HealthSummary,
    *,
    copy_trading_enabled: bool,
    metaapi_enabled: bool,
    token_configured: bool,
) -> HealthSummary:
    required = copy_trading_enabled
    configured = metaapi_enabled and token_configured
    status = "healthy" if configured else "missing" if required else "disabled"
    component = {
        "role": "metaapi",
        "status": status,
        "heartbeat_at": None,
        "stream_lag": 0,
        "pending_count": 0,
        "last_error": None if configured else "MetaApi copy execution is not configured.",
    }
    if not required or configured:
        return HealthSummary(
            status=health.status,
            ready=health.ready,
            components=[*health.components, component],
            issues=health.issues,
        )
    return HealthSummary(
        status="action_required",
        ready=False,
        components=[*health.components, component],
        issues=[*health.issues, "MetaApi copy execution is not configured."],
    )


def add_telegram_connection_health(
    health: HealthSummary,
    *,
    connection_states: list[str],
) -> HealthSummary:
    normalized = [getattr(state, "value", state) for state in connection_states]
    if not normalized:
        component = {
            "role": "telegram-connection",
            "status": "not_configured",
            "heartbeat_at": None,
            "stream_lag": 0,
            "pending_count": 0,
            "last_error": None,
        }
        return HealthSummary(
            status=health.status,
            ready=health.ready,
            components=[*health.components, component],
            issues=health.issues,
        )
    authorization_required = [
        state for state in normalized if state == "reauthentication_required"
    ]
    reconnecting = [state for state in normalized if state == "disconnected"]
    if not authorization_required and not reconnecting:
        component = {
            "role": "telegram-connection",
            "status": "healthy",
            "heartbeat_at": None,
            "stream_lag": 0,
            "pending_count": 0,
            "last_error": None,
        }
        return HealthSummary(
            status=health.status,
            ready=health.ready,
            components=[*health.components, component],
            issues=health.issues,
        )
    if reconnecting and not authorization_required:
        component = {
            "role": "telegram-connection",
            "status": "reconnecting",
            "heartbeat_at": None,
            "stream_lag": 0,
            "pending_count": 0,
            "last_error": "Telegram is reconnecting automatically.",
        }
        return HealthSummary(
            status="degraded",
            ready=False,
            components=[*health.components, component],
            issues=[*health.issues, "Telegram is reconnecting automatically."],
        )
    component = {
        "role": "telegram-connection",
        "status": "reauthentication_required",
        "heartbeat_at": None,
        "stream_lag": 0,
        "pending_count": 0,
        "last_error": "Reconnect Telegram to resume reading copy signals.",
    }
    return HealthSummary(
        status="action_required",
        ready=False,
        components=[*health.components, component],
        issues=[*health.issues, "Reconnect Telegram to resume reading copy signals."],
    )


def build_launch_readiness(
    health: HealthSummary,
    *,
    dead_letter_count: int,
    uncertain_intent_ages: list[int],
    active_intent_ages: list[int],
    active_intent_max_age_seconds: int,
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
        warnings.append("dead_letters_need_review")
    oldest_active_intent = max(active_intent_ages or [0])
    if oldest_active_intent > active_intent_max_age_seconds:
        blockers.append("active_intents")
    elif active_intent_ages:
        warnings.append("broker_action_pending")
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
        oldest_active_intent_seconds=oldest_active_intent,
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
