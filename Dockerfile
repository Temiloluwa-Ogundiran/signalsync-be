FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini main.py ./
COPY alembic ./alembic
COPY src ./src
COPY scripts/docker_start.py ./scripts/docker_start.py
COPY scripts/copy_trading_launch_check.py ./scripts/copy_trading_launch_check.py
COPY scripts/copy_trading_synthetic_signal.py ./scripts/copy_trading_synthetic_signal.py

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["python", "scripts/docker_start.py"]
