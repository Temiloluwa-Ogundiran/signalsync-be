import os
import socket
import subprocess
import sys
import time
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


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    parsed = urlparse(database_url)

    wait_for("postgres", parsed.hostname or "postgres", parsed.port or 5432)
    wait_for("redis", "redis", 6379)

    # Run migrations once before forking workers. In a multi-replica deploy this
    # should ideally be a dedicated init/pre-deploy step so only one process migrates.
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    workers = os.environ.get("WEB_CONCURRENCY", _default_workers())
    port = os.environ.get("PORT", "8000")

    # Replace this process with Gunicorn managing N Uvicorn workers so the API can
    # serve concurrent requests across CPU cores instead of a single event loop.
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "main:app",
            "--worker-class",
            "uvicorn.workers.UvicornWorker",
            "--workers",
            workers,
            "--bind",
            f"0.0.0.0:{port}",
            "--timeout",
            os.environ.get("GUNICORN_TIMEOUT", "60"),
            "--graceful-timeout",
            os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"),
            "--max-requests",
            os.environ.get("GUNICORN_MAX_REQUESTS", "2000"),
            "--max-requests-jitter",
            os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "200"),
            # Trust the proxy/LB so X-Forwarded-For yields the real client IP
            # (used for rate limiting). Restrict to the LB subnet in production.
            "--forwarded-allow-ips",
            os.environ.get("FORWARDED_ALLOW_IPS", "*"),
            "--access-logfile",
            "-",
        ],
    )


if __name__ == "__main__":
    main()
