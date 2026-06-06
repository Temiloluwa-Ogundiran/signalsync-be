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


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    parsed = urlparse(database_url)

    wait_for("postgres", parsed.hostname or "postgres", parsed.port or 5432)
    wait_for("redis", "redis", 6379)

    subprocess.run(["alembic", "upgrade", "head"], check=True)
    subprocess.run(
        [
            "uvicorn",
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            os.environ.get("PORT", "8000"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
