import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.domains.copy_trading.metaapi_connections import (
    MetaApiConnectionManager,
    MetaApiRuntime,
)
from app.domains.copy_trading.metaapi_client import build_metaapi


def test_python_sdk_client_is_not_pinned_to_an_account_region(monkeypatch) -> None:
    calls = []

    def metaapi(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "metaapi_cloud_sdk", SimpleNamespace(MetaApi=metaapi))

    build_metaapi("secret-token")

    assert calls == [{"token": "secret-token"}]
from tests.fakes.fake_metaapi import FakeStreamingAccount, FakeStreamingApi


def test_concurrent_acquire_opens_and_synchronizes_once() -> None:
    async def scenario() -> None:
        account = FakeStreamingAccount("account-1")
        api = FakeStreamingApi(account)
        manager = MetaApiConnectionManager(
            api=api, timeout_seconds=30, idle_seconds=300
        )

        first, second, third = await asyncio.gather(
            manager.acquire("account-1"),
            manager.acquire("account-1"),
            manager.acquire("account-1"),
        )

        assert first is second is third
        assert api.get_account_calls == 1
        assert first.connect_calls == 1
        assert first.wait_synchronized_calls == 1
        await manager.shutdown()

    asyncio.run(scenario())


def test_undeployed_account_is_rejected_before_opening_stream() -> None:
    async def scenario() -> None:
        account = FakeStreamingAccount("account-1", state="UNDEPLOYED")
        manager = MetaApiConnectionManager(
            api=FakeStreamingApi(account), timeout_seconds=30, idle_seconds=300
        )

        with pytest.raises(ConnectionError, match="not deployed"):
            await manager.acquire("account-1")

        assert account.created_connections == []
        await manager.shutdown()

    asyncio.run(scenario())


def test_unhealthy_connection_is_closed_and_replaced() -> None:
    async def scenario() -> None:
        account = FakeStreamingAccount("account-1")
        manager = MetaApiConnectionManager(
            api=FakeStreamingApi(account), timeout_seconds=30, idle_seconds=300
        )
        first = await manager.acquire("account-1")

        await manager.mark_unhealthy("account-1")
        second = await manager.acquire("account-1")

        assert second is not first
        assert first.close_calls == 1
        assert len(account.created_connections) == 2
        await manager.shutdown()

    asyncio.run(scenario())


def test_close_account_cancels_an_inflight_connection_attempt() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        account = FakeStreamingAccount("account-1")
        api = FakeStreamingApi(account)
        manager = MetaApiConnectionManager(
            api=api, timeout_seconds=30, idle_seconds=300
        )

        async def wait_until_released(_options: dict) -> None:
            await release.wait()

        original_factory = account.get_streaming_connection

        def blocking_factory():
            connection = original_factory()
            connection.wait_synchronized = wait_until_released
            return connection

        account.get_streaming_connection = blocking_factory
        opening = asyncio.create_task(manager.acquire("account-1"))
        while not account.created_connections:
            await asyncio.sleep(0)

        await manager.close_account("account-1")

        with pytest.raises(asyncio.CancelledError):
            await opening
        assert account.created_connections[0].close_calls == 1
        assert manager.warm_count == 0

    asyncio.run(scenario())


def test_health_monitor_disconnect_is_replaced_before_use() -> None:
    async def scenario() -> None:
        account = FakeStreamingAccount("account-1")
        manager = MetaApiConnectionManager(
            api=FakeStreamingApi(account), timeout_seconds=30, idle_seconds=300
        )
        first = await manager.acquire("account-1")
        first.health_monitor = type(
            "HealthMonitor", (), {"health_status": {"connected": False}}
        )()

        second = await manager.acquire("account-1")

        assert second is not first
        assert first.close_calls == 1
        await manager.shutdown()

    asyncio.run(scenario())


def test_idle_eviction_and_shutdown_close_connections() -> None:
    async def scenario() -> None:
        now = [100.0]
        account = FakeStreamingAccount("account-1")
        manager = MetaApiConnectionManager(
            api=FakeStreamingApi(account),
            timeout_seconds=30,
            idle_seconds=10,
            clock=lambda: now[0],
        )
        first = await manager.acquire("account-1")
        now[0] = 111.0

        assert await manager.evict_idle() == 1
        assert first.close_calls == 1
        second = await manager.acquire("account-1")
        await manager.shutdown()

        assert second.close_calls == 1
        assert manager.warm_count == 0

    asyncio.run(scenario())


def test_process_runtime_keeps_connection_on_one_owned_event_loop() -> None:
    account = FakeStreamingAccount("account-1")
    api = FakeStreamingApi(account)
    runtime = MetaApiRuntime(
        api_factory=lambda: api,
        timeout_seconds=30,
        idle_seconds=300,
    )

    first = runtime.acquire("account-1")
    second = runtime.acquire("account-1")
    result = runtime.run(first.create_market_buy_order("EURUSD", 0.1))
    runtime.shutdown()

    assert first is second
    assert api.get_account_calls == 1
    assert first.close_calls == 1
    assert result["orderId"] == "order-1"


def test_process_runtime_builds_metaapi_inside_its_running_event_loop() -> None:
    account = FakeStreamingAccount("account-1")
    api = FakeStreamingApi(account)
    factory_observations: list[bool] = []

    def api_factory():
        factory_observations.append(asyncio.get_running_loop().is_running())
        return api

    runtime = MetaApiRuntime(
        api_factory=api_factory,
        timeout_seconds=30,
        idle_seconds=300,
    )

    runtime.acquire("account-1")
    runtime.shutdown()

    assert factory_observations == [True]
