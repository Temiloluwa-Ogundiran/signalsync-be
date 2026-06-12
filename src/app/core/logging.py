import json
import logging
import sys
from contextvars import ContextVar

# Correlation id for the in-flight request. Set by RequestIdMiddleware and attached
# to every log record so logs can be traced across a single request.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "line": f"{record.module}:{record.lineno}",
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(debug: bool = False, json_logs: bool = True) -> None:
    """Configure the root logger for the application."""
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d "
                "| req=%(request_id)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Reduce noise from third-party libs
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if debug else logging.WARNING
    )
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("alembic").setLevel(logging.INFO)


logger = logging.getLogger("synctrades")
