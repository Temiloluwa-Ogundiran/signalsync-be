import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.domains.copy_trading.client_ids import metaapi_client_id
from app.domains.copy_trading.metaapi_broker import MetaApiBroker
from app.domains.copy_trading.symbols import broker_symbol_from_metaapi, normalize_volume


class FakeConnection:
    def __init__(self) -> None:
        self.calls = []
        self.terminal_state = SimpleNamespace(
            specifications=[
                {
                    "symbol": "XAUUSDm",
                    "contractSize": 100,
                    "minVolume": 0.01,
                    "maxVolume": 50,
                    "volumeStep": 0.01,
                    "tradeMode": "SYMBOL_TRADE_MODE_FULL",
                    "fillingModes": ["SYMBOL_FILLING_IOC"],
                    "executionMode": "SYMBOL_TRADE_EXECUTION_MARKET",
                }
            ],
            positions=[{"id": "position-1", "clientId": "client-id"}],
            orders=[{"id": "order-1", "clientId": "client-id"}],
        )
        self.history_storage = SimpleNamespace(
            deals=[{"id": "deal-1", "clientId": "client-id"}]
        )

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"orderId": "order-1", "dealId": "deal-1", "positionId": "position-1"}

        return call


def test_client_id_is_deterministic_shaped_and_collision_resistant() -> None:
    route = uuid.uuid4()
    intent = uuid.uuid4()

    first = metaapi_client_id(route_id=route, intent_id=intent)

    assert first == metaapi_client_id(route_id=route, intent_id=intent)
    assert len(first) <= 31
    assert len(first.split("_")) == 3
    assert all(segment.isalnum() and segment.isascii() for segment in first.split("_"))
    values = {
        metaapi_client_id(route_id=route, intent_id=uuid.uuid4())
        for _ in range(5_000)
    }
    assert len(values) == 5_000


def test_metaapi_specification_and_volume_normalization() -> None:
    symbol = broker_symbol_from_metaapi(FakeConnection().terminal_state.specifications[0])

    assert symbol.name == "XAUUSDm"
    assert symbol.contract_size == Decimal("100")
    assert symbol.min_volume == Decimal("0.01")
    assert symbol.max_volume == Decimal("50")
    assert symbol.volume_step == Decimal("0.01")
    assert normalize_volume(Decimal("0.126"), symbol) == Decimal("0.12")
    assert normalize_volume(Decimal("0.001"), symbol) == Decimal("0.01")
    assert normalize_volume(Decimal("60"), symbol) == Decimal("50")


def test_broker_maps_market_pending_and_management_actions() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        broker = MetaApiBroker(connection)
        options = {"clientId": "strategy_position_order", "magic": 123, "comment": "copy"}

        market = await broker.market_order(
            direction="buy", symbol="XAUUSDm", volume=0.1, stop_loss=2300, take_profit=2400, options=options
        )
        await broker.pending_order(
            direction="sell", order_type="limit", symbol="XAUUSDm", volume=0.1,
            open_price=2400, stop_loss=2450, take_profit=2300, options=options,
        )
        await broker.modify_position("position-1", stop_loss=2350, take_profit=2400)
        await broker.close_position("position-1", volume=0.05, options=options)
        await broker.close_position("position-1", options=options)
        await broker.cancel_order("order-1")

        assert market == {"order_id": "order-1", "deal_id": "deal-1", "position_id": "position-1"}
        assert [call[0] for call in connection.calls] == [
            "create_market_buy_order",
            "create_limit_sell_order",
            "modify_position",
            "close_position_partially",
            "close_position",
            "cancel_order",
        ]
        assert connection.calls[0][1][-1] == options

    asyncio.run(scenario())


def test_broker_converts_serialized_decimal_stops_for_metaapi() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        broker = MetaApiBroker(connection)

        await broker.market_order(
            direction="sell",
            symbol="EURUSD",
            volume=0.01,
            stop_loss="1.14500",
            take_profit="1.13000",
        )
        await broker.modify_position(
            "position-1",
            stop_loss=Decimal("1.14000"),
            take_profit="1.13250",
        )

        assert connection.calls[0][1][2:4] == (1.145, 1.13)
        assert connection.calls[1][1][1:3] == (1.14, 1.1325)

    asyncio.run(scenario())


def test_broker_exposes_synchronized_terminal_truth() -> None:
    broker = MetaApiBroker(FakeConnection())

    assert broker.find_order(client_id="client-id")["id"] == "order-1"
    assert broker.find_position(client_id="client-id")["id"] == "position-1"
    assert broker.find_deal(client_id="client-id")["id"] == "deal-1"
    assert broker.symbols()[0].name == "XAUUSDm"
