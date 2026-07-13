from decimal import Decimal

from app.domains.copy_trading.symbols import (
    BrokerSymbol,
    broker_symbol_from_metaapi,
)


def _numeric_stop(value):
    if value is None or isinstance(value, (int, float, dict)):
        return value
    if isinstance(value, (str, Decimal)):
        return float(value)
    raise TypeError(f"Unsupported MetaApi stop value type: {type(value).__name__}")


def _normalized_result(result: dict | None) -> dict[str, str | None]:
    value = result or {}
    return {
        "order_id": value.get("orderId") or value.get("order"),
        "deal_id": value.get("dealId") or value.get("deal"),
        "position_id": value.get("positionId") or value.get("position"),
    }


class MetaApiBroker:
    def __init__(self, connection) -> None:
        self.connection = connection

    def symbols(self) -> list[BrokerSymbol]:
        return [
            broker_symbol_from_metaapi(item)
            for item in self.connection.terminal_state.specifications
        ]

    def positions(self) -> list[dict]:
        return list(self.connection.terminal_state.positions)

    def orders(self) -> list[dict]:
        return list(self.connection.terminal_state.orders)

    def find_order(self, *, client_id: str, order_id: str | None = None):
        return next(
            (
                item
                for item in self.connection.terminal_state.orders
                if (order_id and str(item.get("id")) == str(order_id))
                or item.get("clientId") == client_id
                or item.get("client_id") == client_id
            ),
            None,
        )

    def find_position(self, *, client_id: str, position_id: str | None = None):
        return next(
            (
                item
                for item in self.connection.terminal_state.positions
                if (position_id and str(item.get("id")) == str(position_id))
                or item.get("clientId") == client_id
                or item.get("client_id") == client_id
            ),
            None,
        )

    def find_deal(self, *, client_id: str, deal_id: str | None = None):
        history_storage = getattr(self.connection, "history_storage", None)
        deals = getattr(history_storage, "deals", []) if history_storage else []
        return next(
            (
                item
                for item in deals
                if (deal_id and str(item.get("id")) == str(deal_id))
                or item.get("clientId") == client_id
                or item.get("client_id") == client_id
            ),
            None,
        )

    async def market_order(
        self,
        *,
        direction: str,
        symbol: str,
        volume: float,
        stop_loss=None,
        take_profit=None,
        options: dict | None = None,
    ) -> dict:
        method = (
            self.connection.create_market_buy_order
            if direction.lower() == "buy"
            else self.connection.create_market_sell_order
        )
        return _normalized_result(
            await method(
                symbol,
                volume,
                _numeric_stop(stop_loss),
                _numeric_stop(take_profit),
                options or {},
            )
        )

    async def pending_order(
        self,
        *,
        direction: str,
        order_type: str,
        symbol: str,
        volume: float,
        open_price: float,
        stop_loss=None,
        take_profit=None,
        options: dict | None = None,
    ) -> dict:
        method_name = f"create_{order_type.lower()}_{direction.lower()}_order"
        method = getattr(self.connection, method_name)
        return _normalized_result(
            await method(
                symbol,
                volume,
                open_price,
                _numeric_stop(stop_loss),
                _numeric_stop(take_profit),
                options or {},
            )
        )

    async def modify_position(self, position_id: str, *, stop_loss=None, take_profit=None) -> dict:
        return _normalized_result(
            await self.connection.modify_position(
                position_id,
                _numeric_stop(stop_loss),
                _numeric_stop(take_profit),
            )
        )

    async def close_position(
        self, position_id: str, *, volume: float | None = None, options: dict | None = None
    ) -> dict:
        if volume is not None:
            result = await self.connection.close_position_partially(
                position_id, volume, options or {}
            )
        else:
            result = await self.connection.close_position(position_id, options or {})
        return _normalized_result(result)

    async def cancel_order(self, order_id: str) -> dict:
        return _normalized_result(await self.connection.cancel_order(order_id))
