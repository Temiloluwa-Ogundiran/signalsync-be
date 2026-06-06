# Synctrades Backend Dokploy Compose Cleanup Design

Date: 2026-06-06
Repo: `synctrades-be`
Branch: `staging`

## Goal

Replace the current backend deployment scaffolding with a clean Dokploy-ready runtime that reflects how the backend actually works today.

The result should:

- provide a single `docker-compose.yml` for Dokploy
- include only the services the current backend deployment needs
- run database migrations automatically before the API starts
- keep Postgres and Redis internal to the deployment
- remove stale or misleading environment variables from `.env.example`

## Current Problems

The current deployment shape is misleading in a few ways:

- `docker-compose.yml` only provisions Redis and does not describe a deployable backend stack
- there is no production `Dockerfile` for the API service
- `.env.example` mixes current settings, legacy comments, Supabase-hosted Postgres guidance, and variables that no longer matter to the deployed runtime
- the env template suggests scheduling and worker-related settings that are not part of the current manual-sync backend behavior

This makes deployment harder than it should be and increases the risk of configuring the wrong things in Dokploy.

## Decisions

### Runtime topology

The deployment will use a single compose file with exactly three services:

- `api`
- `postgres`
- `redis`

There will be:

- no Traefik container
- no Celery worker
- no Celery beat
- no separate migration container
- no admin sidecars

Dokploy will expose the `api` service directly and domain binding will be handled in the Dokploy UI.

### API startup contract

The `api` service will:

1. wait for Postgres and Redis to become reachable
2. run `alembic upgrade head`
3. start Uvicorn bound to `0.0.0.0:8000`

This makes schema migration part of normal deploy startup and avoids image/schema drift.

### Environment contract

`.env.example` will only document variables that are supported by current code paths and still matter to a real deployment.

The file will include:

#### Required core variables

- `DATABASE_URL`
- `SECRET_KEY`
- `ENCRYPTION_KEY`
- `FRONTEND_URL`
- `CORS_ALLOWED_ORIGINS`
- `IS_PRODUCTION`
- `AUTO_SEED_ON_STARTUP`
- `MT5_CORE_URL`
- `MT5_CORE_INTERNAL_SHARED_SECRET`

#### Optional current runtime variables

- `APP_NAME`
- `DEBUG`
- `PORT`
- `ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `REFRESH_TOKEN_EXPIRE_DAYS`
- `EMAIL_VERIFY_EXPIRY_HOURS`
- `MT5_CORE_POLL_TIMEOUT_SECONDS`
- `MT5_CORE_POLL_INTERVAL_SECONDS`
- `INITIAL_SYNC_LOOKBACK_DAYS`

#### Optional email variables

- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME`

#### Optional storage variables

These remain because current upload and journal-media code paths still read them:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_STREAM_AVATARS_BUCKET`
- `SUPABASE_STREAM_BANNERS_BUCKET`
- `SUPABASE_POST_MEDIA_BUCKET`
- `JOURNAL_VOICE_BUCKET`
- `JOURNAL_IMAGES_BUCKET`
- `MEDIA_SIGNED_URL_TTL_SECONDS`
- `VOICE_SIGNED_URL_TTL_SECONDS`
- `IMAGE_SIGNED_URL_TTL_SECONDS`
- `STORAGE_SERVICE_BASE_URL`
- `STORAGE_SERVICE_UPLOAD_PATH`
- `STORAGE_SERVICE_API_KEY`
- `STORAGE_SERVICE_PRESIGNED_TTL_SECONDS`

#### Optional legacy MetaAPI variables

These stay only because current backend code still contains active MetaAPI sync paths:

- `METAAPI_TOKEN`
- `METAAPI_VERIFY_SSL`
- `METAAPI_PROVISIONING_BASE_URL`
- `METAAPI_CLIENT_BASE_URL`
- `METAAPI_ACCOUNT_ENGINE`
- `METAAPI_ACCOUNT_MAGIC`
- `METAAPI_DEALS_TIMEOUT_SECONDS`
- `METAAPI_DEALS_MAX_RETRIES`
- `METAAPI_DEALS_RETRY_BACKOFF_SECONDS`
- `METAAPI_DEALS_CHUNK_DAYS`

### Variables to remove from `.env.example`

The template will remove variables that are dead, misleading, or not part of the deployed API runtime:

- `BACKEND_URL`
- `DEEP_LINK_SCHEME`
- `SYNC_INTERVAL_MINUTES`
- `ACTIVE_USER_WINDOW_MINUTES`
- `USER_ACTIVITY_TOUCH_MIN_INTERVAL_SECONDS`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

Even though the repo still contains Celery code and activity middleware references, those values are not part of the intended Dokploy deployment contract for the current manual-sync system.

## Compose Shape

### `api`

Responsibilities:

- build the backend image from the repo
- load env from `.env`
- depend on healthy `postgres` and `redis`
- run the startup script that performs migration and then starts Uvicorn

Ports:

- expose container port `8000`

### `postgres`

Responsibilities:

- provide the primary application database

Persistence:

- named volume for PostgreSQL data

Networking:

- no public port publishing required for Dokploy operation

### `redis`

Responsibilities:

- provide the internal Redis instance requested for the stack

Persistence:

- named volume for Redis data

Networking:

- no public port publishing required for Dokploy operation

## Build Artifacts

The cleanup will add the missing runtime artifacts required by the compose file:

- `Dockerfile`
- startup script for wait/migrate/start behavior

The image should:

- use Python 3.11
- install project dependencies from `pyproject.toml` and `uv.lock`
- copy the application code and Alembic files
- run with a non-root-friendly, production-oriented command path

## Verification

The implementation will be considered complete when:

- `docker compose config` renders successfully
- the compose file clearly defines only `api`, `postgres`, and `redis`
- `.env.example` contains only currently supported variables
- the startup command path is consistent with the app entrypoints and Alembic layout in the repo

## Non-Goals

This cleanup does not:

- reintroduce automatic recurring sync
- redesign current storage integrations
- remove MetaAPI code paths from the backend
- introduce Traefik or ingress configuration into compose
- add a separate worker deployment model
