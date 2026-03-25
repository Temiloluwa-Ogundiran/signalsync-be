def start() -> None:
    # Periodic sync is handled by Celery beat + worker.
    return None


def stop() -> None:
    # No in-process worker to stop.
    return None
