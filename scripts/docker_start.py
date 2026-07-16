"""
Entry-point for all process types in one image.

Set PROCESS_TYPE to select the role for this container:
  api     (default) — run Alembic migrations then start the Gunicorn/Uvicorn API
  ai      — start the AI-only process group (same app, no migrations, larger timeout)
  worker  — start the Celery worker
"""
import os
import socket
import subprocess
import sys
import time
import logging
from urllib.parse import urlparse


def wait_for(name: str, host: str, port: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name} is reachable at {host}:{port}")
                return
        except OSError:
            time.sleep(1)
    print(f"Timed out waiting for {name} at {host}:{port}", file=sys.stderr)
    sys.exit(1)


def _default_workers() -> str:
    return str(2 * (os.cpu_count() or 1) + 1)


def _worker_count(env_name: str, default: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    workers = value or default
    # Gunicorn reads WEB_CONCURRENCY while importing its config, before it parses
    # the explicit --workers arg. Keep this env var valid even when Compose or
    # the platform injects it as an empty string.
    os.environ["WEB_CONCURRENCY"] = workers
    return workers


def wait_for_deps() -> None:
    database_url = os.environ["DATABASE_URL"]
    parsed = urlparse(database_url)
    wait_for("postgres", parsed.hostname or "postgres", parsed.port or 5432)

    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    redis_parsed = urlparse(redis_url)
    wait_for("redis", redis_parsed.hostname or "redis", redis_parsed.port or 6379)


def start_api() -> None:
    wait_for_deps()

    # Run migrations once before forking workers. In a multi-replica deploy this
    # should ideally be a dedicated init/pre-deploy step so only one process migrates.
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    workers = _worker_count("WEB_CONCURRENCY", _default_workers())
    port = os.environ.get("PORT", "8000")

    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "main:app",
            "--worker-class", "uvicorn.workers.UvicornWorker",
            "--workers", workers,
            "--bind", f"0.0.0.0:{port}",
            "--timeout", os.environ.get("GUNICORN_TIMEOUT", "60"),
            "--graceful-timeout", os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"),
            "--max-requests", os.environ.get("GUNICORN_MAX_REQUESTS", "2000"),
            "--max-requests-jitter", os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "200"),
            "--forwarded-allow-ips", os.environ.get("FORWARDED_ALLOW_IPS", "*"),
            "--access-logfile", "-",
        ],
    )


def start_ai() -> None:
    """
    AI process group: same app, no migrations, tuned for LLM workload.

    - Fewer workers (I/O-bound, not CPU-bound — each worker blocks for 3-20s on LLM)
    - Much larger timeout (LLM calls can take 20s+; default 60s would kill them)
    - Does NOT run Alembic — the API service owns migrations
    """
    wait_for_deps()

    # AI workers are I/O-bound waiting on OpenAI, so 2-4 workers per instance is
    # correct. Scale horizontally (more Railway replicas) not vertically.
    workers = _worker_count("AI_WEB_CONCURRENCY", "4")
    port = os.environ.get("PORT", "8000")

    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "main:app",
            "--worker-class", "uvicorn.workers.UvicornWorker",
            "--workers", workers,
            "--bind", f"0.0.0.0:{port}",
            "--timeout", "120",           # LLM calls can take up to 20s + tool loops
            "--graceful-timeout", "30",
            "--max-requests", "500",
            "--max-requests-jitter", "50",
            "--forwarded-allow-ips", os.environ.get("FORWARDED_ALLOW_IPS", "*"),
            "--access-logfile", "-",
        ],
    )


def start_worker() -> None:
    wait_for_deps()

    os.execvp(
        "celery",
        [
            "celery",
            "-A", "app.worker.celery_app",
            "worker",
            "--loglevel", "info",
            "--concurrency", os.environ.get("CELERY_CONCURRENCY", "8"),
        ],
    )


def start_copy_worker(role: str) -> None:
    wait_for_deps()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)
    # Engine.IO logs the full WebSocket URL, including MetaApi's auth token.
    # Keep application and broker errors visible while suppressing that transport noise.
    logging.getLogger("engineio.client").setLevel(logging.WARNING)
    logging.getLogger("socketio.client").setLevel(logging.WARNING)
    from app.domains.copy_trading.worker_runtime import run_process
    run_process(role)


def main() -> None:
    process_type = os.environ.get("PROCESS_TYPE", "api").lower()

    handlers = {
        "api": start_api,
        "ai": start_ai,
        "worker": start_worker,
        "telegram-session": lambda: start_copy_worker("telegram-session"),
        "copy-signal": lambda: start_copy_worker("copy-signal"),
        "copy-execution": lambda: start_copy_worker("copy-execution"),
    }

    handler = handlers.get(process_type)
    if handler is None:
        print(f"Unknown PROCESS_TYPE={process_type!r}. Use: api | ai | worker | telegram-session | copy-signal | copy-execution", file=sys.stderr)
        sys.exit(1)

    print(f"Starting process type: {process_type}")
    handler()


if __name__ == "__main__":
    main()
