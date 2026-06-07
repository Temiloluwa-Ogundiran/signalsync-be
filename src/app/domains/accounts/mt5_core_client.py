from __future__ import annotations

import asyncio
import httpx
from datetime import datetime
from typing import Any, Optional
from app.core.config import settings

class Mt5CoreClientError(Exception):
    """Base exception for Mt5CoreClient."""
    pass

class Mt5CoreClientTimeout(Mt5CoreClientError):
    """Raised when polling mt5-core job times out."""
    pass

class Mt5CoreClientJobFailed(Mt5CoreClientError):
    """Raised when enqueued job fails."""
    pass


class Mt5CoreClientHttpError(Mt5CoreClientError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class Mt5CoreClientRateLimited(Mt5CoreClientHttpError):
    pass


class Mt5CoreClientBackpressure(Mt5CoreClientHttpError):
    pass

class Mt5CoreClient:
    """Robust, polling-based client for mt5-core engine integration."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        shared_secret: Optional[str] = None,
        poll_timeout: Optional[int] = None,
        poll_interval: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or settings.MT5_CORE_URL).rstrip("/")
        self.shared_secret = shared_secret or settings.MT5_CORE_INTERNAL_SHARED_SECRET
        self.poll_timeout = poll_timeout if poll_timeout is not None else settings.MT5_CORE_POLL_TIMEOUT_SECONDS
        self.poll_interval = poll_interval if poll_interval is not None else settings.MT5_CORE_POLL_INTERVAL_SECONDS

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Internal-Shared-Secret": self.shared_secret,
        }

    def _new_client(self) -> httpx.AsyncClient:
        """Create a fresh httpx client with keep-alive disabled.

        A new client is used per-request so that keep-alive connection
        reuse across POST → GET boundaries cannot cause RemoteProtocolError
        ("Server disconnected without sending a response").
        """
        return httpx.AsyncClient(
            headers=self._headers(),
            timeout=10.0,
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> int | None:
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return int(retry_after)
        except ValueError:
            return None

    def _raise_submit_error(self, response: httpx.Response) -> None:
        detail: Any
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            code = str(detail.get("code") or detail.get("error_code") or "MT5_CORE_ERROR")
            message = str(detail.get("message") or response.text or "MT5 core request failed.")
        elif isinstance(detail, str):
            code = "MT5_CORE_ERROR"
            message = detail
        else:
            code = "MT5_CORE_ERROR"
            message = response.text or "MT5 core request failed."

        retry_after_seconds = self._retry_after_seconds(response)
        if response.status_code == 429:
            raise Mt5CoreClientRateLimited(
                code,
                message,
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds,
            )
        if response.status_code == 503:
            raise Mt5CoreClientBackpressure(
                code,
                message,
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds,
            )
        raise Mt5CoreClientHttpError(
            code,
            message,
            status_code=response.status_code,
            retry_after_seconds=retry_after_seconds,
        )

    async def verify_credentials(
        self,
        *,
        account_id: str,
        login: str,
        password: str,
        server: str,
        broker: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Submit a credential verification job and poll until completion."""
        payload = {
            "account_id": account_id,
            "cluster_role": "journal",
            "login": login,
            "password": password,
            "server": server,
            "broker": broker,
            "metadata": metadata or {},
        }
        
        # Use a dedicated client just for the POST so the connection is not
        # shared with the subsequent polling GET requests.
        async with self._new_client() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/accounts/verify",
                    json=payload,
                )
                if response.is_error:
                    self._raise_submit_error(response)
                data = response.json()
            except Mt5CoreClientHttpError:
                raise
            except Exception as e:
                raise Mt5CoreClientError(f"Failed to submit account verification job: {e}") from e

        job_id = data["job_id"]
        return await self._poll_job(job_id)

    async def submit_history_sync(
        self,
        *,
        account_id: str,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        credentials: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit a history sync job and poll until completion."""
        # Convert datetime to ISO-8601 strings if provided
        from_time_str = from_time.isoformat() if from_time else None
        to_time_str = to_time.isoformat() if to_time else None

        payload = {
            "account_id": account_id,
            "cluster_role": "journal",
            "from_time": from_time_str,
            "to_time": to_time_str,
            "include_deals": True,
            "include_orders": True,
            "credentials": {
                "login": str(credentials["login"]),
                "password": str(credentials["password"]),
                "server": str(credentials["server"]),
                "broker": credentials.get("broker"),
            },
            "correlation_id": correlation_id,
        }

        # Use a dedicated client just for the POST.
        async with self._new_client() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/history/sync",
                    json=payload,
                )
                if response.is_error:
                    self._raise_submit_error(response)
                data = response.json()
            except Mt5CoreClientHttpError:
                raise
            except Exception as e:
                raise Mt5CoreClientError(f"Failed to submit history sync job: {e}") from e

        job_id = data["job_id"]
        return await self._poll_job(job_id)

    async def get_open_positions(
        self,
        *,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "login": str(credentials["login"]),
            "password": str(credentials["password"]),
            "server": str(credentials["server"]),
            "broker": credentials.get("broker"),
        }

        async with self._new_client() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/account/positions",
                    json=payload,
                )
                if response.is_error:
                    self._raise_submit_error(response)
                data = response.json()
            except Mt5CoreClientHttpError:
                raise
            except Exception as e:
                raise Mt5CoreClientError(
                    f"Failed to submit open positions read job: {e}"
                ) from e

        job_id = data["job_id"]
        result = await self._poll_job(job_id)
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            return result["data"]
        return result

    async def _poll_job(self, job_id: str) -> dict[str, Any]:
        """Poll the status of a job until succeeded or failed.

        Each poll opens a fresh HTTP connection to avoid RemoteProtocolError
        that occurs when a keep-alive connection is closed by the server
        between polls.
        """
        start_time = asyncio.get_event_loop().time()
        
        while True:
            try:
                # Fresh client per poll — avoids reusing a stale keep-alive conn.
                async with self._new_client() as client:
                    response = await client.get(
                        f"{self.base_url}/jobs/{job_id}",
                    )
                    response.raise_for_status()
                    job_status_resp = response.json()
            except httpx.RemoteProtocolError:
                # Server closed the connection before responding — transient,
                # will retry after poll_interval.
                pass
            except Exception as e:
                raise Mt5CoreClientError(f"Failed to fetch job status for {job_id}: {e}") from e
            else:
                status = job_status_resp.get("status")
                if status == "succeeded":
                    return job_status_resp.get("result") or {}
                elif status == "failed":
                    raise Mt5CoreClientJobFailed(job_status_resp.get("error") or "Job failed")
            
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= self.poll_timeout:
                raise Mt5CoreClientTimeout(f"Polling job {job_id} timed out after {self.poll_timeout} seconds")
            
            await asyncio.sleep(self.poll_interval)
