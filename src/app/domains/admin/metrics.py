import hmac
import time

from fastapi import HTTPException, Request, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from app.core.config import settings


HTTP_REQUESTS = Counter(
    "tradepartna_http_requests_total",
    "HTTP requests handled by the API.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "tradepartna_http_request_duration_seconds",
    "HTTP request duration by route.",
    ("method", "route"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)


async def observe_http_requests(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_label, "500").inc()
        HTTP_DURATION.labels(request.method, route_label).observe(
            time.perf_counter() - started
        )
        raise
    route = request.scope.get("route")
    route_label = getattr(route, "path", "unmatched")
    HTTP_REQUESTS.labels(request.method, route_label, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, route_label).observe(time.perf_counter() - started)
    return response


def render_metrics(request: Request) -> Response:
    accepted = tuple(
        value
        for value in (
            settings.METRICS_BEARER_TOKEN,
            settings.MT5_CORE_INTERNAL_SHARED_SECRET,
        )
        if value
    )
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not accepted or not any(hmac.compare_digest(supplied, value) for value in accepted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics credentials are invalid.",
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
