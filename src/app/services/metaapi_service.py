from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid
import json
import time
import logging

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)

TRANSIENT_METAAPI_STATUS_CODES = {429, 502, 503, 504}


class MetaApiProvisioningError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        code: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.details = details


class MetaApiService:
    def __init__(self) -> None:
        self.headers = {
            "auth-token": settings.METAAPI_TOKEN,
            "Content-Type": "application/json",
        }

    def _client(self, *, transaction_id: Optional[str] = None) -> httpx.Client:
        headers = dict(self.headers)
        if transaction_id:
            headers["transaction-id"] = transaction_id

        return httpx.Client(
            timeout=30.0,
            headers=headers,
            verify=settings.METAAPI_VERIFY_SSL,
        )

    @staticmethod
    def _extract_error_fields(response: httpx.Response) -> tuple[str, Optional[str], Optional[str]]:
        default_message = f"MetaAPI provisioning failed with status {response.status_code}."
        try:
            payload = response.json()
        except ValueError:
            return default_message, None, None

        if not isinstance(payload, dict):
            return default_message, None, None

        message = str(payload.get("message") or default_message)
        details = payload.get("details")
        code = None
        details_text = None

        if isinstance(details, dict):
            maybe_code = details.get("code")
            if maybe_code is not None:
                code = str(maybe_code)
            try:
                details_text = json.dumps(details, separators=(",", ":"), ensure_ascii=True)
            except (TypeError, ValueError):
                details_text = str(details)
        elif isinstance(details, str):
            code = details
            details_text = details
        elif details is not None:
            try:
                details_text = json.dumps(details, separators=(",", ":"), ensure_ascii=True)
            except (TypeError, ValueError):
                details_text = str(details)

        return message, code, details_text

    def provision_account(
        self,
        *,
        broker_name: str,
        broker_login: str,
        broker_server: str,
        platform: str,
        investor_password: str,
        trader_password: Optional[str],
        account_engine: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> str:
        url = f"{settings.METAAPI_PROVISIONING_BASE_URL}/users/current/accounts"
        transaction_id = uuid.uuid4().hex
        payload = {
            "name": display_name or f"{broker_name} {broker_login}",
            "type": account_engine or settings.METAAPI_ACCOUNT_ENGINE,
            "login": broker_login,
            "password": trader_password or investor_password,
            "server": broker_server,
            "platform": platform.lower(),
            "magic": settings.METAAPI_ACCOUNT_MAGIC,
            "keywords": [broker_name],
        }

        with self._client(transaction_id=transaction_id) as client:
            response = client.post(url, json=payload)
            if response.is_error:
                message, code, details_text = self._extract_error_fields(response)
                raise MetaApiProvisioningError(
                    status_code=response.status_code,
                    message=message,
                    code=code,
                    details=details_text,
                )
            data = response.json()

        account_id = data.get("id") if isinstance(data, dict) else None
        if not account_id:
            raise ValueError("MetaAPI did not return an account id.")
        return str(account_id)

    def get_deals(
        self,
        account_id: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        from_value = from_dt or datetime.fromisoformat("1970-01-01T00:00:00+00:00")
        to_value = to_dt or datetime.now(timezone.utc)

        if settings.METAAPI_DEALS_CHUNK_DAYS <= 0:
            chunks = [(from_value, to_value)]
        else:
            chunks = []
            cursor = from_value
            while cursor < to_value:
                chunk_end = min(
                    to_value,
                    cursor + timedelta(days=settings.METAAPI_DEALS_CHUNK_DAYS),
                )
                chunks.append((cursor, chunk_end))
                cursor = chunk_end

        all_deals: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for chunk_from, chunk_to in chunks:
            payload = self._get_deals_window(account_id, chunk_from, chunk_to)
            deals = payload if isinstance(payload, list) else payload.get("deals", []) if isinstance(payload, dict) else []

            for deal in deals:
                dedupe_key = str(
                    deal.get("id")
                    or deal.get("broker_trade_id")
                    or deal.get("ticket")
                    or f"{deal.get('time')}-{deal.get('symbol')}-{deal.get('profit')}"
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                all_deals.append(deal)

        return all_deals

    def _get_deals_window(
        self,
        account_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> Any:
        from_value = from_dt.isoformat()
        to_value = to_dt.isoformat() + ("Z" if to_dt.tzinfo is None else "")
        url = f"{settings.METAAPI_CLIENT_BASE_URL}/users/current/accounts/{account_id}/history-deals/time/{from_value}/{to_value}"

        last_exc: Exception | None = None
        for attempt in range(1, settings.METAAPI_DEALS_MAX_RETRIES + 1):
            try:
                with self._client() as client:
                    response = client.get(url, timeout=settings.METAAPI_DEALS_TIMEOUT_SECONDS)

                if response.status_code in TRANSIENT_METAAPI_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"Transient MetaAPI status {response.status_code}",
                        request=response.request,
                        response=response,
                    )

                response.raise_for_status()
                return response.json()
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                should_retry = is_transient_metaapi_error(exc)
                status_code = None
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    status_code = exc.response.status_code

                if not should_retry or attempt == settings.METAAPI_DEALS_MAX_RETRIES:
                    raise

                sleep_seconds = settings.METAAPI_DEALS_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "MetaAPI get_deals retrying | account_id=%s attempt=%s status=%s sleep=%.2fs",
                    account_id,
                    attempt,
                    status_code,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)

        if last_exc is not None:
            raise last_exc

    def get_account_info(self, account_id: str) -> dict[str, Any]:
        url = f"{settings.METAAPI_CLIENT_BASE_URL}/users/current/accounts/{account_id}/account-information"
        with self._client() as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()


def is_transient_metaapi_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in TRANSIENT_METAAPI_STATUS_CODES
    return False


metaapi_service = MetaApiService()
