import json
from dataclasses import dataclass

from app.core.config import settings
from app.domains.billing.models import BillingPlan


MAX_COPY_ACCOUNTS = 10


@dataclass(frozen=True)
class ProductCatalog:
    journal_product_id: str
    copy_product_ids: dict[int, str]

    @classmethod
    def from_settings(cls) -> "ProductCatalog":
        journal = settings.BACHS_JOURNAL_PRODUCT_ID.strip()
        try:
            raw_copy_products = json.loads(settings.BACHS_COPY_PRODUCT_IDS or "{}")
            copy_products = {int(key): str(value).strip() for key, value in raw_copy_products.items()}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("BACHS_COPY_PRODUCT_IDS must be a JSON object") from exc
        if not journal:
            raise ValueError("BACHS_JOURNAL_PRODUCT_ID is required")
        expected = set(range(1, MAX_COPY_ACCOUNTS + 1))
        if set(copy_products) != expected or any(not value for value in copy_products.values()):
            raise ValueError("BACHS_COPY_PRODUCT_IDS must contain tiers 1 through 10")
        product_ids = [journal, *copy_products.values()]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Every Bachs plan tier must use a unique product ID")
        return cls(journal_product_id=journal, copy_product_ids=copy_products)

    def product_for(self, plan: BillingPlan, copy_accounts: int) -> str:
        if plan == BillingPlan.journal:
            return self.journal_product_id
        try:
            return self.copy_product_ids[copy_accounts]
        except KeyError as exc:
            raise ValueError("Copy accounts must be between 1 and 10") from exc

    def entitlement_for(self, product_id: str) -> tuple[BillingPlan, int]:
        if product_id == self.journal_product_id:
            return BillingPlan.journal, 0
        for account_limit, candidate in self.copy_product_ids.items():
            if product_id == candidate:
                return BillingPlan.copy, account_limit
        raise ValueError("Unknown Bachs product ID")
