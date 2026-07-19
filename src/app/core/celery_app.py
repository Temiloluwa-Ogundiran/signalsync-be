from celery import Celery  # type: ignore[import-not-found]

from app.core.config import settings

celery_app = Celery(
    "synctrades",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.journal_sync_tasks",
        "app.tasks.auth_tasks",
        "app.tasks.copy_trading_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "dispatch-pending-journal-bootstraps": {
            "task": "journal.dispatch_pending_bootstraps",
            "schedule": 2.0,
        },
    },
)


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
