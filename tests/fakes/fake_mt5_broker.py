from decimal import Decimal


class FakeMt5Broker:
    def __init__(self) -> None:
        self._orders = {}
        self._timeout_after_accept = False
        self._next_ticket = 1000

    @property
    def order_count(self) -> int:
        return len(self._orders)

    def timeout_after_accepting_once(self) -> None:
        self._timeout_after_accept = True

    def submit(self, client_order_id: str, *, symbol: str, volume: Decimal) -> dict:
        existing = self._orders.get(client_order_id)
        if existing:
            return {**existing, "idempotent_replay": True}
        self._next_ticket += 1
        order = {
            "ticket": self._next_ticket,
            "client_order_id": client_order_id,
            "comment": f"cpid:{client_order_id[:20]}",
            "symbol": symbol,
            "volume": volume,
            "sl": None,
            "tp": None,
        }
        self._orders[client_order_id] = order
        if self._timeout_after_accept:
            self._timeout_after_accept = False
            raise TimeoutError("response lost after broker acceptance")
        return {**order, "idempotent_replay": False}

    def modify(self, ticket: int, *, stop_loss: Decimal, take_profit: Decimal) -> None:
        order = self._by_ticket(ticket)
        order["sl"] = stop_loss
        order["tp"] = take_profit

    def partial_close(self, ticket: int, volume: Decimal) -> None:
        order = self._by_ticket(ticket)
        order["volume"] -= volume

    def positions(self) -> list[dict]:
        return [dict(order) for order in self._orders.values()]

    def _by_ticket(self, ticket: int) -> dict:
        return next(order for order in self._orders.values() if order["ticket"] == ticket)
