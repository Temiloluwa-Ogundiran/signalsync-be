from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "synctrades",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.journal_sync_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "journal-sync-all-accounts": {
        "task": "journal.sync_all_accounts",
        "schedule": max(60, settings.SYNC_INTERVAL_MINUTES * 60),
    },
    # Periodic trigger for accounts synced via the headless MT5 microservice.
    # Fires at the same interval as the MetaAPI sync cycle.
    "journal-sync-all-mt5-accounts": {
        "task": "journal.sync_all_mt5_accounts",
        "schedule": max(60, settings.SYNC_INTERVAL_MINUTES * 60),
    },
}


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
