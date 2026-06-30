from typing import Any

import httpx


PROVISIONING_URL = (
    "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts"
)


def build_metaapi(token: str, *, region: str | None = None):
    from metaapi_cloud_sdk import MetaApi

    options = {"region": region} if region else None
    return MetaApi(token=token, opts=options)


class MetaApiProvisioningHttpClient:
    def __init__(
        self,
        *,
        token: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def create_account(
        self, payload: dict[str, Any], transaction_id: str
    ) -> dict[str, Any] | None:
        response = await self._client.post(
            PROVISIONING_URL,
            headers={
                "auth-token": self._token,
                "transaction-id": transaction_id,
                "accept": "application/json",
            },
            json=payload,
        )
        body = response.json() if response.content else {}
        if response.status_code == 202:
            return None
        if response.status_code == 201:
            return body

        from app.domains.copy_trading.metaapi_provisioning import (
            classify_provisioning_error,
        )

        raise classify_provisioning_error(body, status_code=response.status_code)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
