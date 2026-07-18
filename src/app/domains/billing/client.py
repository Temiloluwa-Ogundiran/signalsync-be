import uuid
from typing import Any

import httpx

from app.core.config import settings


class BachsError(RuntimeError):
    pass


class BachsClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.BACHS_API_KEY
        self.base_url = (base_url or settings.BACHS_API_BASE_URL).rstrip("/")

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        if not self.api_key:
            raise BachsError("Bachs billing is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(idempotency_key=idempotency_key),
                    json=json,
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BachsError("The billing provider could not process the request") from exc
        if not isinstance(payload, dict):
            raise BachsError("The billing provider returned an invalid response")
        return payload

    async def create_checkout(
        self,
        *,
        product_id: str,
        user_id: uuid.UUID,
        email: str,
        name: str | None,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        customer: dict[str, str] = {"email": email}
        if name:
            customer["name"] = name
        request_id = uuid.uuid4().hex
        return await self._request(
            "POST",
            "/v1/checkout-sessions",
            json={
                "product_cart": [{"product_id": product_id, "quantity": 1}],
                "billing_currency": "USD",
                "allowed_payment_method_types": ["card"],
                "success_url": success_url,
                "cancel_url": cancel_url,
                "customer": customer,
                "reference": f"tradepartna-{user_id}-{request_id[:12]}",
                "metadata": {"tradepartna_user_id": str(user_id)},
            },
            idempotency_key=request_id,
        )

    async def change_plan(
        self,
        *,
        subscription_id: str,
        product_id: str,
        proration_behavior: str,
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH",
            f"/v1/subscriptions/{subscription_id}",
            json={"product_id": product_id, "proration_behavior": proration_behavior},
        )

    async def cancel_at_period_end(self, *, subscription_id: str) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"/v1/subscriptions/{subscription_id}",
            json={"cancel_at_period_end": True, "reason": "Customer requested"},
        )
