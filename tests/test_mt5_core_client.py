import json
from decimal import Decimal

import pytest
import httpx
import anyio
from datetime import datetime
import app.domains.accounts.mt5_core_client as mt5_core_client_module
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClient,
    Mt5CoreClientError,
    Mt5CoreClientJobFailed,
    Mt5CoreClientRateLimited,
    Mt5CoreClientTimeout,
)

@pytest.fixture
def client() -> Mt5CoreClient:
    return Mt5CoreClient(
        base_url="http://mt5-core-test",
        shared_secret="secret123",
        poll_timeout=2,
        poll_interval=0.1,
    )


def test_mt5_core_client_reuses_bounded_keepalive_connections(client: Mt5CoreClient) -> None:
    mt5_http_client = client._new_client()
    try:
        pool = mt5_http_client._transport._pool
        assert pool._max_keepalive_connections == 5
        assert pool._keepalive_expiry == 10.0
    finally:
        anyio.run(mt5_http_client.aclose)


@pytest.mark.anyio
async def test_verify_credentials_success(client: Mt5CoreClient, httpx_mock) -> None:
    # Mock submission
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-abc", "status": "queued"},
    )
    # Mock polling status
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-abc",
        json={
            "job_id": "job-abc",
            "job_type": "verify_account",
            "account_id": "acct-1",
            "cluster_role": "journal",
            "status": "succeeded",
            "result": {"verified": True, "balance": 5000.0},
        },
    )

    result = await client.verify_credentials(
        account_id="acct-1",
        login="10001",
        password="pass",
        server="BrokerServer",
    )

    assert result == {"verified": True, "balance": 5000.0}


@pytest.mark.anyio
async def test_verify_credentials_failed_job(client: Mt5CoreClient, httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-abc", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-abc",
        json={
            "job_id": "job-abc",
            "job_type": "verify_account",
            "account_id": "acct-1",
            "cluster_role": "journal",
            "status": "failed",
            "error": "Invalid credentials",
        },
    )

    with pytest.raises(Mt5CoreClientJobFailed) as exc:
        await client.verify_credentials(
            account_id="acct-1",
            login="10001",
            password="pass",
            server="BrokerServer",
        )
    assert "Invalid credentials" in str(exc.value)
    assert len(httpx_mock.get_requests(method="POST")) == 1


@pytest.mark.anyio
async def test_failed_order_job_preserves_uncertain_submission_details(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/orders",
        json={"job_id": "job-order", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-order",
        json={
            "status": "failed",
            "error": "transport lost",
            "error_details": {
                "code": "JOB_EXECUTION_FAILED",
                "message": "transport lost",
                "submission_started": True,
                "uncertain": True,
            },
        },
    )

    with pytest.raises(Mt5CoreClientJobFailed) as captured:
        await client.submit_action(
            path="/orders",
            payload={"symbol": "EURUSD"},
        )

    assert captured.value.submission_started is True
    assert captured.value.uncertain is True


@pytest.mark.anyio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_verify_credentials_retries_one_ipc_failure_then_succeeds(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-ipc-first", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-ipc-first",
        json={
            "status": "failed",
            "worker_available": True,
            "error": "Failed to initialize MT5 terminal: IPC timeout (code=-10005)",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-ipc-retry", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-ipc-retry",
        json={
            "status": "succeeded",
            "worker_available": True,
            "result": {"verified": True},
        },
    )

    result = await client.verify_credentials(
        account_id="acct-1",
        login="10001",
        password="pass",
        server="BrokerServer",
    )

    assert result == {"verified": True}
    assert len(httpx_mock.get_requests(method="POST")) == 2


@pytest.mark.anyio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_verify_credentials_stops_after_two_ipc_failures(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    for suffix in ("first", "retry"):
        httpx_mock.add_response(
            method="POST",
            url="http://mt5-core-test/accounts/verify",
            json={"job_id": f"job-ipc-{suffix}", "status": "queued"},
        )
        httpx_mock.add_response(
            method="GET",
            url=f"http://mt5-core-test/jobs/job-ipc-{suffix}",
            json={
                "status": "failed",
                "worker_available": True,
                "error": "Failed to initialize MT5 terminal: IPC timeout (code=-10005)",
            },
        )

    with pytest.raises(
        mt5_core_client_module.Mt5CoreClientTransientJobFailed
    ):
        await client.verify_credentials(
            account_id="acct-1",
            login="10001",
            password="pass",
            server="BrokerServer",
        )

    assert len(httpx_mock.get_requests(method="POST")) == 2


@pytest.mark.anyio
async def test_verify_credentials_waits_for_temporary_admission_pressure(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    client.poll_timeout = 1
    client.poll_interval = 0.01
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        status_code=429,
        json={
            "detail": {
                "code": "RATE_LIMITED",
                "message": "Submission rate limit exceeded. Please retry shortly.",
            }
        },
        headers={"Retry-After": "0"},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-after-wait", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-after-wait",
        json={
            "job_id": "job-after-wait",
            "job_type": "verify_account",
            "status": "succeeded",
            "result": {"verified": True},
        },
    )

    result = await client.verify_credentials(
        account_id="acct-1",
        login="10001",
        password="pass",
        server="BrokerServer",
    )

    assert result == {"verified": True}


@pytest.mark.anyio
async def test_verify_credentials_admission_wait_does_not_consume_processing_timeout(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    client.poll_timeout = 0.01
    client.admission_retry_interval = 0.01
    for _ in range(2):
        httpx_mock.add_response(
            method="POST",
            url="http://mt5-core-test/accounts/verify",
            status_code=429,
            json={
                "detail": {
                    "code": "RATE_LIMITED",
                    "message": "Submission rate limit exceeded.",
                }
            },
        )
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-after-long-admission", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-after-long-admission",
        json={
            "job_id": "job-after-long-admission",
            "status": "succeeded",
            "result": {"verified": True},
        },
    )

    result = await client.verify_credentials(
        account_id="acct-1",
        login="10001",
        password="pass",
        server="BrokerServer",
    )

    assert result == {"verified": True}


@pytest.mark.anyio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_verify_credentials_healthy_queue_wait_does_not_consume_processing_timeout(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    client.poll_timeout = 0.03
    client.poll_interval = 0.01
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-queued-healthy", "status": "queued"},
    )
    for _ in range(8):
        httpx_mock.add_response(
            method="GET",
            url="http://mt5-core-test/jobs/job-queued-healthy",
            json={"status": "queued", "worker_available": True},
        )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-queued-healthy",
        json={"status": "running", "worker_available": True},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-queued-healthy",
        json={
            "status": "succeeded",
            "worker_available": True,
            "result": {"verified": True},
        },
    )

    result = await client.verify_credentials(
        account_id="acct-1",
        login="10001",
        password="pass",
        server="BrokerServer",
    )

    assert result == {"verified": True}


@pytest.mark.anyio
async def test_verify_credentials_fails_when_queued_worker_is_unavailable(
    client: Mt5CoreClient,
    httpx_mock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-worker-down", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/job-worker-down",
        json={"status": "queued", "worker_available": False},
    )

    with pytest.raises(mt5_core_client_module.Mt5CoreClientWorkerUnavailable):
        await client.verify_credentials(
            account_id="acct-1",
            login="10001",
            password="pass",
            server="BrokerServer",
        )


@pytest.mark.anyio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_verify_credentials_timeout(client: Mt5CoreClient, httpx_mock) -> None:
    # Set poll timeout to 0.2 to speed up test
    client.poll_timeout = 0.2
    
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/accounts/verify",
        json={"job_id": "job-abc", "status": "queued"},
    )
    
    # Return running continuously by adding multiple responses
    for _ in range(5):
        httpx_mock.add_response(
            method="GET",
            url="http://mt5-core-test/jobs/job-abc",
            json={
                "job_id": "job-abc",
                "job_type": "verify_account",
                "account_id": "acct-1",
                "cluster_role": "journal",
                "status": "running",
                "worker_available": True,
            },
        )

    with pytest.raises(Mt5CoreClientTimeout) as exc:
        await client.verify_credentials(
            account_id="acct-1",
            login="10001",
            password="pass",
            server="BrokerServer",
        )
    assert "timed out" in str(exc.value)


@pytest.mark.anyio
async def test_submit_history_sync_success(client: Mt5CoreClient, httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/history/sync",
        json={"job_id": "sync-123", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/sync-123",
        json={
            "job_id": "sync-123",
            "job_type": "sync_history",
            "account_id": "acct-1",
            "cluster_role": "journal",
            "status": "succeeded",
            "result": {"deals": [{"ticket": "100"}]},
        },
    )

    result = await client.submit_history_sync(
        account_id="acct-1",
        from_time=datetime(2026, 5, 20),
        previous_balance=Decimal("501103.19"),
        known_closed_trade_count=9,
        known_latest_closed_at=datetime(2026, 5, 20, 9, 30),
        credentials={"login": "10001", "password": "p", "server": "s"},
    )

    assert result == {"deals": [{"ticket": "100"}]}
    request = httpx_mock.get_request(
        method="POST",
        url="http://mt5-core-test/history/sync",
    )
    assert request is not None
    payload = json.loads(request.content)
    assert payload["previous_balance"] == 501103.19
    assert payload["known_closed_trade_count"] == 9
    assert payload["known_latest_closed_at"] == "2026-05-20T09:30:00"


@pytest.mark.anyio
async def test_submit_history_sync_raises_rate_limited_error(client: Mt5CoreClient, httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/history/sync",
        status_code=429,
        json={
            "detail": {
                "code": "RATE_LIMITED",
                "message": "Submission rate limit exceeded. Please retry shortly.",
            }
        },
        headers={"Retry-After": "3"},
    )

    with pytest.raises(Mt5CoreClientRateLimited) as exc:
        await client.submit_history_sync(
            account_id="acct-1",
            credentials={"login": "10001", "password": "p", "server": "s"},
        )

    assert exc.value.retry_after_seconds == 3


@pytest.mark.anyio
async def test_get_open_positions_success(client: Mt5CoreClient, httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://mt5-core-test/account/positions",
        json={"job_id": "positions-123", "status": "queued"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://mt5-core-test/jobs/positions-123",
        json={
            "job_id": "positions-123",
            "job_type": "refresh_positions_snapshot",
            "status": "succeeded",
            "result": {
                "operation": "refresh_positions_snapshot",
                "data": {
                    "as_of": "2026-06-07T02:00:00Z",
                    "positions": [
                        {
                            "position_id": "1001",
                            "symbol": "BTCUSD",
                            "side": "buy",
                            "profit": -10.33,
                        }
                    ],
                },
            },
        },
    )

    result = await client.get_open_positions(
        credentials={"login": "10001", "password": "p", "server": "s"},
    )

    assert result["as_of"] == "2026-06-07T02:00:00Z"
    assert result["positions"][0]["position_id"] == "1001"
