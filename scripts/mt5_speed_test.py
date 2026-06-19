#!/usr/bin/env python
"""Standalone MT5 (mt5-core) speed test.

Measures latency of the real mt5-core engine for the cycle:
    connect acct A -> "delete" (DB-side, no-op vs mt5-core) -> reconnect acct A -> switch acct B

Each "connect" is a POST /accounts/verify followed by polling GET /jobs/{id} until
`succeeded`. We time the submit (admission) separately from the poll-to-result so you
can see where MT5 actually spends time.

Usage:
    # Reads MT5_CORE_URL + MT5_CORE_INTERNAL_SHARED_SECRET from .env (via settings).
    # Pass accounts inline (repeat --account) or via a JSON file (--accounts-file).

    uv run python scripts/mt5_speed_test.py \
        --account "login:password:server:broker?" \
        --account "login2:password2:server2" \
        --rounds 3

    uv run python scripts/mt5_speed_test.py --accounts-file accounts.json --rounds 5

accounts.json format:
    [{"login": "123", "password": "x", "server": "Broker-Demo", "broker": "Broker"}, ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Make `app` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.domains.accounts.mt5_core_client import (  # noqa: E402
    Mt5CoreClient,
    Mt5CoreClientError,
)


@dataclass
class Account:
    login: str
    password: str
    server: str
    broker: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.login}@{self.server}"


@dataclass
class Sample:
    leg: str          # connect / reconnect / switch
    account: str
    submit_ms: float  # time to POST /accounts/verify and get a job_id back
    poll_ms: float    # time spent polling /jobs/{id} until succeeded/failed
    total_ms: float
    ok: bool
    detail: str = ""


@dataclass
class TimedResult:
    submit_ms: float
    poll_ms: float
    total_ms: float
    result: Any
    error: Optional[str] = None


class TimedMt5Client(Mt5CoreClient):
    """Subclass that splits submit latency from poll latency for one verify call."""

    async def timed_verify(self, acct: Account) -> TimedResult:
        payload = {
            "account_id": f"speedtest-{uuid.uuid4().hex[:12]}",
            "cluster_role": "journal",
            "login": acct.login,
            "password": acct.password,
            "server": acct.server,
            "broker": acct.broker,
            "metadata": {"source": "mt5_speed_test"},
        }
        t0 = time.monotonic()
        async with self._new_client() as client:
            try:
                data = await self._submit_verify_with_short_wait(client, payload)
            except Mt5CoreClientError as exc:
                submit_ms = (time.monotonic() - t0) * 1000
                return TimedResult(submit_ms, 0.0, submit_ms, None, error=str(exc))

            submit_ms = (time.monotonic() - t0) * 1000
            job_id = data["job_id"]

            t1 = time.monotonic()
            try:
                result = await self._poll_job(client, job_id)
            except Mt5CoreClientError as exc:
                poll_ms = (time.monotonic() - t1) * 1000
                return TimedResult(
                    submit_ms, poll_ms, submit_ms + poll_ms, None, error=str(exc)
                )
            poll_ms = (time.monotonic() - t1) * 1000
            return TimedResult(submit_ms, poll_ms, submit_ms + poll_ms, result)


def parse_account_arg(raw: str) -> Account:
    parts = raw.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError(
            f"--account must be login:password:server[:broker], got {raw!r}"
        )
    login, password, server = parts[0], parts[1], parts[2]
    broker = parts[3] if len(parts) > 3 and parts[3] else None
    return Account(login=login, password=password, server=server, broker=broker)


def load_accounts(args: argparse.Namespace) -> list[Account]:
    accounts: list[Account] = list(args.account or [])
    if args.accounts_file:
        data = json.loads(Path(args.accounts_file).read_text())
        for entry in data:
            accounts.append(
                Account(
                    login=str(entry["login"]),
                    password=str(entry["password"]),
                    server=str(entry["server"]),
                    broker=entry.get("broker"),
                )
            )
    return accounts


def fmt(ms: float) -> str:
    return f"{ms:8.1f}ms"


def summarize(samples: list[Sample]) -> None:
    print("\n" + "=" * 78)
    print("PER-CALL RESULTS")
    print("=" * 78)
    print(f"{'leg':<11}{'account':<24}{'submit':>10}{'poll':>10}{'total':>10}  ok")
    print("-" * 78)
    for s in samples:
        mark = "✓" if s.ok else "✗"
        print(
            f"{s.leg:<11}{s.account:<24}{fmt(s.submit_ms)}{fmt(s.poll_ms)}"
            f"{fmt(s.total_ms)}  {mark}"
            + (f"  {s.detail}" if s.detail else "")
        )

    ok = [s for s in samples if s.ok]
    if not ok:
        print("\nNo successful calls — nothing to aggregate.")
        return

    def stats(vals: list[float]) -> str:
        vals_sorted = sorted(vals)
        p95 = vals_sorted[min(len(vals_sorted) - 1, int(round(0.95 * (len(vals_sorted) - 1))))]
        return (
            f"min={min(vals):.0f}  median={statistics.median(vals):.0f}  "
            f"p95={p95:.0f}  max={max(vals):.0f}  mean={statistics.mean(vals):.0f}"
        )

    print("\n" + "=" * 78)
    print(f"AGGREGATE  ({len(ok)} successful / {len(samples)} total)   [ms]")
    print("=" * 78)
    print(f"  submit : {stats([s.submit_ms for s in ok])}")
    print(f"  poll   : {stats([s.poll_ms for s in ok])}")
    print(f"  total  : {stats([s.total_ms for s in ok])}")

    by_leg: dict[str, list[Sample]] = {}
    for s in ok:
        by_leg.setdefault(s.leg, []).append(s)
    print("\n  by leg (total ms):")
    for leg, group in by_leg.items():
        print(f"    {leg:<11} {stats([s.total_ms for s in group])}")


async def run(args: argparse.Namespace) -> int:
    accounts = load_accounts(args)
    if not accounts:
        print("ERROR: no accounts provided. Use --account or --accounts-file.", file=sys.stderr)
        return 2

    if not settings.MT5_CORE_URL or not settings.MT5_CORE_INTERNAL_SHARED_SECRET:
        print(
            "ERROR: MT5_CORE_URL / MT5_CORE_INTERNAL_SHARED_SECRET are not set.\n"
            "       Put them in synctrades-be/.env, then re-run.",
            file=sys.stderr,
        )
        return 2

    print(f"mt5-core: {settings.MT5_CORE_URL}")
    print(f"accounts: {[a.label for a in accounts]}")
    print(f"rounds:   {args.rounds}\n")

    client = TimedMt5Client(
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
    )
    samples: list[Sample] = []
    acct_a = accounts[0]
    acct_b = accounts[1] if len(accounts) > 1 else None

    async def measure(leg: str, acct: Account) -> None:
        print(f"[{leg:<10}] {acct.label} ...", end="", flush=True)
        r = await client.timed_verify(acct)
        ok = r.error is None
        detail = r.error or ""
        if ok and isinstance(r.result, dict) and "verified" in r.result:
            detail = f"verified={r.result.get('verified')}"
        samples.append(
            Sample(leg, acct.label, r.submit_ms, r.poll_ms, r.total_ms, ok, detail)
        )
        print(f" {fmt(r.total_ms)}  ({'ok' if ok else 'FAIL: ' + detail})")

    for rnd in range(1, args.rounds + 1):
        print(f"--- round {rnd}/{args.rounds} ---")
        await measure("connect", acct_a)
        # "delete the account": mt5-core is stateless per job — nothing to tear down
        # engine-side. The journal deletes the row in its own DB. We log the boundary.
        print("[delete    ] (DB-side purge; no mt5-core call) — simulating teardown")
        await measure("reconnect", acct_a)
        if acct_b is not None:
            await measure("switch", acct_b)
        print()

    summarize(samples)
    return 0 if all(s.ok for s in samples) else 1


def main() -> int:
    p = argparse.ArgumentParser(description="MT5 (mt5-core) speed test")
    p.add_argument(
        "--account",
        action="append",
        type=parse_account_arg,
        metavar="login:password:server[:broker]",
        help="Account to test (repeatable). First is acct A, second is acct B.",
    )
    p.add_argument("--accounts-file", help="JSON file with a list of accounts")
    p.add_argument("--rounds", type=int, default=3, help="Cycles to run (default 3)")
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=settings.MT5_CORE_POLL_TIMEOUT_SECONDS,
        help="Max seconds to poll a job before giving up",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        help="Seconds between job-status polls (default 0.25 for tighter timing)",
    )
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
