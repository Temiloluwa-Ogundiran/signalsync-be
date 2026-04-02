from app.core.celery_app import celery_app
import app.models  # noqa: F401 — registers all SQLAlchemy models so string-based relationships resolve
import app.tasks.journal_sync_tasks  # noqa: F401

__all__ = ["celery_app"]
