import pytest
import httpx
from datetime import datetime
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
    
    # Return queued continuously by adding multiple responses
    for _ in range(5):
        httpx_mock.add_response(
            method="GET",
            url="http://mt5-core-test/jobs/job-abc",
            json={
                "job_id": "job-abc",
                "job_type": "verify_account",
                "account_id": "acct-1",
                "cluster_role": "journal",
                "status": "queued",
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
        credentials={"login": "10001", "password": "p", "server": "s"},
    )

    assert result == {"deals": [{"ticket": "100"}]}


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
