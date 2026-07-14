from datetime import datetime, timezone

from sqlalchemy import select

from app.domains.copy_trading.models import CopyExecutionMetric


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _elapsed(start: datetime | None, end: datetime | None) -> int | None:
    return max(0, round((end - start).total_seconds() * 1000)) if start and end else None


def record_execution_metric(db, *, intent, route, status: str, resolved_at: datetime | None = None) -> CopyExecutionMetric:
    existing = db.execute(select(CopyExecutionMetric).where(CopyExecutionMetric.intent_id == intent.id)).scalar_one_or_none()
    if existing:
        return existing
    timing = (intent.request_payload or {}).get("_telemetry") or {}
    telegram_at = _parse(timing.get("telegram_at"))
    ingested_at = _parse(timing.get("ingested_at"))
    validated_at = _parse(timing.get("validated_at"))
    submitted_at = intent.submitted_at
    resolved_at = resolved_at or intent.resolved_at or datetime.now(timezone.utc)
    metric = CopyExecutionMetric(
        user_id=intent.user_id,
        route_id=route.id,
        intent_id=intent.id,
        correlation_id=str(timing.get("correlation_id") or ""),
        action=str(intent.request_payload.get("action") or "unknown"),
        symbol=intent.request_payload.get("symbol"),
        status=status,
        telegram_at=telegram_at,
        ingested_at=ingested_at,
        validated_at=validated_at,
        submitted_at=submitted_at,
        resolved_at=resolved_at,
        ingestion_ms=_elapsed(telegram_at, ingested_at),
        assembly_ms=_elapsed(ingested_at, validated_at),
        broker_ms=_elapsed(submitted_at, resolved_at),
        total_ms=_elapsed(telegram_at, resolved_at),
    )
    db.add(metric)
    return metric


def percentile(values: list[int], percent: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return ordered[index]
