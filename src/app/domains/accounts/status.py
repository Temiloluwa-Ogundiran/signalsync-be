from dataclasses import dataclass
from typing import Literal

from app.domains.accounts.models import TradingAccountConnectionState


AccountSyncSeverity = Literal["success", "info", "pending", "warning", "error"]


@dataclass(frozen=True)
class AccountSyncStatus:
    code: str
    severity: AccountSyncSeverity
    headline: str
    detail: str
    action: str | None = None


def describe_account_sync_status(
    *,
    connection_state: TradingAccountConnectionState,
    sync_provider: str,
    last_sync_outcome: str | None,
    closed_trade_count: int,
    sync_error_message: str | None,
    bootstrap_error_message: str | None,
) -> AccountSyncStatus:
    if sync_provider == "csv_import":
        return AccountSyncStatus(
            code="imported",
            severity="success",
            headline="Imported",
            detail="Trade history was imported from a file.",
        )

    if connection_state == TradingAccountConnectionState.pending_verification:
        return AccountSyncStatus(
            code="pending_verification",
            severity="pending",
            headline="Verifying credentials",
            detail="We are checking the MT5 account credentials.",
        )

    if connection_state == TradingAccountConnectionState.bootstrapping:
        return AccountSyncStatus(
            code="bootstrapping",
            severity="pending",
            headline="Syncing history",
            detail="Initial trade history import is running.",
        )

    if connection_state == TradingAccountConnectionState.verification_failed:
        return AccountSyncStatus(
            code="verification_failed",
            severity="error",
            headline="Account authorization failed",
            detail=sync_error_message
            or "Check the account number, broker server, and investor password.",
            action="Update the credentials, then reconnect the account.",
        )

    if connection_state == TradingAccountConnectionState.bootstrap_failed:
        return AccountSyncStatus(
            code="bootstrap_failed",
            severity="warning",
            headline="Connected with sync warning",
            detail=bootstrap_error_message
            or sync_error_message
            or "History sync did not complete.",
            action="Try resyncing the account.",
        )

    if last_sync_outcome == "success_empty" and closed_trade_count == 0:
        return AccountSyncStatus(
            code="ready_empty",
            severity="info",
            headline="Connected",
            detail="No closed trades found yet.",
            action="Close a trade in MT5, then resync.",
        )

    return AccountSyncStatus(
        code="ready",
        severity="success",
        headline="Connected",
        detail="Account is connected and ready.",
    )
