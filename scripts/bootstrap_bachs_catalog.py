"""Create or reuse TradePartna's recurring Bachs products.

Run with BACHS_API_KEY set. The script is idempotent: products are matched by
the stable `tradepartna_plan_key` metadata value before anything is created.
"""

import json
import os
import sys

import httpx


API_KEY = os.environ.get("BACHS_API_KEY", "").strip()
BASE_URL = os.environ.get("BACHS_API_BASE_URL", "https://sandbox-api.bachs.io").rstrip("/")


def request(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    response = client.request(method, f"{BASE_URL}{path}", **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Bachs returned an invalid response")
    return payload


def desired_products() -> list[dict]:
    products = [
        {
            "key": "journal",
            "name": "TradePartna Journal",
            "description": "Monthly access to the trading journal and analytics.",
            "amount": "17.00",
        }
    ]
    for accounts in range(1, 11):
        amount = 30 + (accounts - 1) * 20
        products.append(
            {
                "key": f"copy_{accounts}",
                "name": f"TradePartna Copy Trading - {accounts} MT5 Account"
                + ("s" if accounts > 1 else ""),
                "description": "Monthly copy trading, unlimited Telegram channels, and journal access.",
                "amount": f"{amount}.00",
            }
        )
    return products


def main() -> int:
    if not API_KEY:
        print("BACHS_API_KEY is required", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    with httpx.Client(headers=headers, timeout=20.0) as client:
        existing_payload = request(client, "GET", "/v1/products?limit=100&include_archived=false")
        existing = {
            item.get("metadata", {}).get("tradepartna_plan_key"): item
            for item in existing_payload.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
        }
        resolved: dict[str, str] = {}
        for product in desired_products():
            found = existing.get(product["key"])
            if found:
                resolved[product["key"]] = str(found["id"])
                continue
            created = request(
                client,
                "POST",
                "/v1/products",
                json={
                    "name": product["name"],
                    "description": product["description"],
                    "price": {
                        "price_type": "fixed",
                        "currency": "USD",
                        "amount": product["amount"],
                    },
                    "billing_cycle": {"interval": "month", "frequency": 1},
                    "metadata": {"tradepartna_plan_key": product["key"]},
                },
            )
            resolved[product["key"]] = str(created["id"])

    copy_products = {str(index): resolved[f"copy_{index}"] for index in range(1, 11)}
    print(f"BACHS_JOURNAL_PRODUCT_ID={resolved['journal']}")
    print("BACHS_COPY_PRODUCT_IDS=" + json.dumps(copy_products, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
