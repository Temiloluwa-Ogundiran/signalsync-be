FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini main.py ./
COPY alembic ./alembic
COPY src ./src
COPY scripts/docker_start.py ./scripts/docker_start.py

EXPOSE 8000

CMD ["python", "scripts/docker_start.py"]
