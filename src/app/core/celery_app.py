from celery import Celery  # type: ignore[import-not-found]

from app.core.config import settings

celery_app = Celery(
    "synctrades",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.journal_sync_tasks",
        "app.tasks.auth_tasks",
        "app.tasks.guard_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

# Partna Guard: the always-on watcher fan-out. This is the ONE scheduled-sync path
# in the app (see the RULES.md Guard exception). Interval is the
# GUARD_POLL_INTERVAL_SECONDS knob — a watchdog must keep checking, so this beat
# entry must stay enabled.
celery_app.conf.beat_schedule = {
    "guard-enqueue-polls": {
        "task": "guard.enqueue_polls",
        "schedule": float(settings.GUARD_POLL_INTERVAL_SECONDS),
    },
}


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
