import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.core.database import SessionLocal  # noqa: E402
from app.repositories import trading_account_repo  # noqa: E402
from app.services.journal_sync_service import sync_account_deals  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile journal trades by re-syncing with strict closed-trade filtering "
            "and cleanup of stale rows in the chosen lookback window."
        )
    )
    parser.add_argument(
        "--account-id",
        action="append",
        default=[],
        help="Trading account UUID to reconcile. Can be specified multiple times.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Reconcile all syncable accounts.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="How many days to reconcile. Default: 30.",
    )
    return parser.parse_args()


def load_target_account_ids(args: argparse.Namespace) -> list[uuid.UUID]:
    if not args.all and not args.account_id:
        raise ValueError("Provide at least one --account-id or use --all.")

    with SessionLocal() as db:
        if args.all:
            return [account.id for account in trading_account_repo.list_syncable_accounts(db)]

    ids: list[uuid.UUID] = []
    for raw in args.account_id:
        ids.append(uuid.UUID(raw))
    return ids


def main() -> None:
    args = parse_args()
    account_ids = load_target_account_ids(args)

    results = []
    for account_id in account_ids:
        with SessionLocal() as db:
            account = trading_account_repo.get_by_id(db, account_id)
            if account is None:
                results.append(
                    {
                        "account_id": str(account_id),
                        "status": "skipped",
                        "reason": "account_not_found_or_disconnected",
                    }
                )
                continue

            result = sync_account_deals(
                db,
                account=account,
                lookback_days=args.lookback_days,
            )
            results.append(
                {
                    "account_id": str(account_id),
                    "status": "ok",
                    "inserted_trades": result.inserted_trades,
                    "touched_trading_dates": result.touched_trading_dates,
                }
            )

    print(json.dumps({"status": "ok", "results": results}, indent=2))


if __name__ == "__main__":
    main()
