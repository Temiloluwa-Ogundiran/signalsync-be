# Dokploy Compose Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the backend's placeholder deployment scaffolding with a Dokploy-ready `api + postgres + redis` stack and a truthful `.env.example`.

**Architecture:** Build a single production API image, start it through a small migration-first entrypoint, and keep Postgres and Redis as internal compose services. Trim `.env.example` down to only variables that still have live code paths and matter for current deployment behavior.

**Tech Stack:** Docker Compose, Python 3.11, FastAPI, Alembic, PostgreSQL, Redis, uv

---

## File Map

- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/Dockerfile`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/scripts/docker-start.sh`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`
- Test with: `docker compose config`

### Task 1: Add production container startup path

**Files:**
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/Dockerfile`
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/scripts/docker-start.sh`

- [ ] **Step 1: Create the startup script**

```sh
#!/bin/sh
set -eu

python - <<'PY'
import socket
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


db = urlparse(__import__("os").environ["DATABASE_URL"])
wait_for("postgres", db.hostname or "postgres", db.port or 5432)
wait_for("redis", "redis", 6379)
PY

uv run alembic upgrade head
exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

- [ ] **Step 2: Create the Dockerfile**

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini main.py ./
COPY alembic ./alembic
COPY src ./src
COPY scripts/docker-start.sh ./scripts/docker-start.sh

RUN chmod +x ./scripts/docker-start.sh

EXPOSE 8000

CMD ["./scripts/docker-start.sh"]
```

- [ ] **Step 3: Verify the new files read cleanly**

Run:

```powershell
Get-Content C:\Users\USER\Documents\SyncTrade\synctrades-be\Dockerfile
Get-Content C:\Users\USER\Documents\SyncTrade\synctrades-be\scripts\docker-start.sh
```

Expected: both files show the migration-first startup path and no placeholder content.

### Task 2: Replace compose with the Dokploy runtime

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`

- [ ] **Step 1: Replace the compose file with the lean stack**

```yaml
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile
    env_file:
      - .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: synctrades
      POSTGRES_USER: synctrades
      POSTGRES_PASSWORD: synctrades
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synctrades -d synctrades"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped
    volumes:
      - synctrades_postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 10
    restart: unless-stopped
    volumes:
      - synctrades_redis_data:/data

volumes:
  synctrades_postgres_data:
  synctrades_redis_data:
```

- [ ] **Step 2: Verify compose renders**

Run:

```powershell
docker compose -f C:\Users\USER\Documents\SyncTrade\synctrades-be\docker-compose.yml config
```

Expected: rendered config succeeds and shows only `api`, `postgres`, and `redis`.

### Task 3: Rewrite `.env.example` to match live supported behavior

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`

- [ ] **Step 1: Replace the env template with a deployment-focused version**

```env
APP_NAME=SyncTrades API
DEBUG=false
IS_PRODUCTION=true
PORT=8000
AUTO_SEED_ON_STARTUP=true

DATABASE_URL=postgresql://synctrades:synctrades@postgres:5432/synctrades

SECRET_KEY=change-me-to-a-long-random-secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=14
ENCRYPTION_KEY=

FRONTEND_URL=https://your-frontend-domain.com
CORS_ALLOWED_ORIGINS=https://your-frontend-domain.com

MT5_CORE_URL=http://mt5-quant-server:8000
MT5_CORE_INTERNAL_SHARED_SECRET=change-me
MT5_CORE_POLL_TIMEOUT_SECONDS=120
MT5_CORE_POLL_INTERVAL_SECONDS=2
INITIAL_SYNC_LOOKBACK_DAYS=14

EMAIL_VERIFY_EXPIRY_HOURS=24
SMTP_SERVER=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
EMAIL_FROM=
EMAIL_FROM_NAME=SyncTrades

SUPABASE_URL=
SUPABASE_SERVICE_KEY=
SUPABASE_STREAM_AVATARS_BUCKET=stream-avatars
SUPABASE_STREAM_BANNERS_BUCKET=stream-banners
SUPABASE_POST_MEDIA_BUCKET=post-media
JOURNAL_VOICE_BUCKET=journal-voice-notes
JOURNAL_IMAGES_BUCKET=journal-images
MEDIA_SIGNED_URL_TTL_SECONDS=3600
VOICE_SIGNED_URL_TTL_SECONDS=3600
IMAGE_SIGNED_URL_TTL_SECONDS=3600

STORAGE_SERVICE_BASE_URL=
STORAGE_SERVICE_UPLOAD_PATH=/api/upload
STORAGE_SERVICE_API_KEY=
STORAGE_SERVICE_PRESIGNED_TTL_SECONDS=600

METAAPI_TOKEN=
METAAPI_VERIFY_SSL=true
METAAPI_PROVISIONING_BASE_URL=https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai
METAAPI_CLIENT_BASE_URL=https://mt-client-api-v1.london.agiliumtrade.ai
METAAPI_ACCOUNT_ENGINE=cloud-g2
METAAPI_ACCOUNT_MAGIC=10001
METAAPI_DEALS_TIMEOUT_SECONDS=45
METAAPI_DEALS_MAX_RETRIES=3
METAAPI_DEALS_RETRY_BACKOFF_SECONDS=1.5
METAAPI_DEALS_CHUNK_DAYS=7
```

- [ ] **Step 2: Verify removed variables are gone**

Run:

```powershell
rg -n "BACKEND_URL|DEEP_LINK_SCHEME|SYNC_INTERVAL_MINUTES|ACTIVE_USER_WINDOW_MINUTES|USER_ACTIVITY_TOUCH_MIN_INTERVAL_SECONDS|CELERY_BROKER_URL|CELERY_RESULT_BACKEND" C:\Users\USER\Documents\SyncTrade\synctrades-be\.env.example
```

Expected: no matches.

### Task 4: Final verification and commit

**Files:**
- Verify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/Dockerfile`
- Verify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/scripts/docker-start.sh`
- Verify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/docker-compose.yml`
- Verify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`

- [ ] **Step 1: Run the full verification sweep**

Run:

```powershell
docker compose -f C:\Users\USER\Documents\SyncTrade\synctrades-be\docker-compose.yml config
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be diff -- Dockerfile scripts/docker-start.sh docker-compose.yml .env.example
```

Expected: compose config succeeds and the diff only contains the intended deployment cleanup.

- [ ] **Step 2: Commit the deployment cleanup**

Run:

```powershell
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be add Dockerfile scripts/docker-start.sh docker-compose.yml .env.example
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be commit -m "chore: add dokploy backend deployment stack"
```

Expected: one clean commit containing the compose, Dockerfile, startup script, and env cleanup.
