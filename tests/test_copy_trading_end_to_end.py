from dataclasses import dataclass, field
from decimal import Decimal

from app.domains.copy_trading.execution import client_order_id_for_key
from tests.fakes.fake_copy_parser import FakeCopyParser, FakeParsedAction
from tests.fakes.fake_mt5_broker import FakeMt5Broker


@dataclass
class Conversation:
    opening_message_id: int
    symbol: str
    direction: str
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = field(default_factory=list)
    submitted_client_ids: list[str] = field(default_factory=list)


class CopyPipeline:
    def __init__(
        self,
        parser: FakeCopyParser,
        broker: FakeMt5Broker,
        *,
        require_sl: bool = True,
        multiple_tp: bool = True,
    ) -> None:
        self.parser = parser
        self.broker = broker
        self.require_sl = require_sl
        self.multiple_tp = multiple_tp
        self.conversations: dict[str, Conversation] = {}
        self.uncertain_client_ids: set[str] = set()

    def receive(self, message_id: int) -> str:
        action = self.parser.parse(message_id)
        if action.action == "open":
            conversation = Conversation(
                opening_message_id=message_id,
                symbol=action.symbol or "",
                direction=action.direction or "",
                stop_loss=action.stop_loss,
            )
            self.conversations[conversation.symbol] = conversation
            return self._submit_if_ready(conversation)

        conversation = self._target(action.symbol)
        if conversation is None:
            return "ambiguous"
        if action.action == "set_sl":
            conversation.stop_loss = action.stop_loss
            if conversation.submitted_client_ids:
                for client_id in conversation.submitted_client_ids:
                    order = self.broker.find_by_client_order_id(client_id)
                    self.broker.modify(
                        order["ticket"],
                        stop_loss=action.stop_loss,
                        take_profit=order["tp"],
                    )
                return "updated"
            return self._submit_if_ready(conversation)
        if action.action == "add_tp" and action.take_profit is not None:
            conversation.take_profits.append(action.take_profit)
            if not self.multiple_tp:
                return "ignored"
            return self._submit(conversation, leg=f"tp:{message_id}", tp=action.take_profit)
        return "ignored"

    def recover(self) -> None:
        for client_id in list(self.uncertain_client_ids):
            order = self.broker.find_by_client_order_id(client_id)
            if order:
                self.broker.submit(
                    client_id,
                    symbol=order["symbol"],
                    volume=order["volume"],
                )
                self.uncertain_client_ids.remove(client_id)

    def _target(self, symbol: str | None) -> Conversation | None:
        if symbol:
            return self.conversations.get(symbol)
        active = list(self.conversations.values())
        return active[0] if len(active) == 1 else None

    def _submit_if_ready(self, conversation: Conversation) -> str:
        if self.require_sl and conversation.stop_loss is None:
            return "waiting"
        return self._submit(conversation, leg="open")

    def _submit(
        self,
        conversation: Conversation,
        *,
        leg: str,
        tp: Decimal | None = None,
    ) -> str:
        client_id = client_order_id_for_key(
            f"route:test:{conversation.opening_message_id}:{leg}"
        )
        if client_id in conversation.submitted_client_ids:
            return "already_submitted"
        try:
            result = self.broker.submit(
                client_id,
                symbol=conversation.symbol,
                volume=Decimal("0.10"),
            )
        except TimeoutError:
            conversation.submitted_client_ids.append(client_id)
            self.uncertain_client_ids.add(client_id)
            return "uncertain"
        conversation.submitted_client_ids.append(client_id)
        if conversation.stop_loss is not None or tp is not None:
            self.broker.modify(
                result["ticket"],
                stop_loss=conversation.stop_loss,
                take_profit=tp,
            )
        return "submitted"


def test_split_signal_opens_once_with_later_sl() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction("open", symbol="XAUUSD", direction="buy"),
            2: FakeParsedAction(
                "set_sl", symbol="XAUUSD", stop_loss=Decimal("2310")
            ),
        }
    )
    broker = FakeMt5Broker()
    pipeline = CopyPipeline(parser, broker)

    assert pipeline.receive(1) == "waiting"
    assert pipeline.receive(2) == "submitted"
    assert broker.order_count == 1
    assert broker.positions()[0]["sl"] == Decimal("2310")


def test_timeout_after_acceptance_reconciles_without_duplicate() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction(
                "open",
                symbol="EURUSD",
                direction="buy",
                stop_loss=Decimal("1.08"),
            )
        }
    )
    broker = FakeMt5Broker()
    broker.timeout_after_accepting_once()
    pipeline = CopyPipeline(parser, broker)

    assert pipeline.receive(1) == "uncertain"
    pipeline.recover()

    assert broker.order_count == 1
    assert pipeline.uncertain_client_ids == set()


def test_restart_recovers_unresolved_intent() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction(
                "open",
                symbol="GBPUSD",
                direction="sell",
                stop_loss=Decimal("1.30"),
            )
        }
    )
    broker = FakeMt5Broker()
    broker.timeout_after_accepting_once()
    first = CopyPipeline(parser, broker)
    assert first.receive(1) == "uncertain"

    restarted = CopyPipeline(parser, broker)
    restarted.uncertain_client_ids = set(first.uncertain_client_ids)
    restarted.recover()

    assert broker.order_count == 1
    assert restarted.uncertain_client_ids == set()


def test_two_symbols_never_share_context() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction("open", symbol="EURUSD", direction="buy"),
            2: FakeParsedAction("open", symbol="XAUUSD", direction="sell"),
            3: FakeParsedAction(
                "set_sl", symbol="XAUUSD", stop_loss=Decimal("2350")
            ),
        }
    )
    broker = FakeMt5Broker()
    pipeline = CopyPipeline(parser, broker)

    pipeline.receive(1)
    pipeline.receive(2)
    pipeline.receive(3)

    assert pipeline.conversations["EURUSD"].stop_loss is None
    assert pipeline.conversations["XAUUSD"].stop_loss == Decimal("2350")
    assert broker.positions()[0]["symbol"] == "XAUUSD"


def test_ambiguous_symbol_less_update_moves_no_money() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction("open", symbol="EURUSD", direction="buy"),
            2: FakeParsedAction("open", symbol="GBPUSD", direction="sell"),
            3: FakeParsedAction("set_sl", stop_loss=Decimal("1.20")),
        }
    )
    broker = FakeMt5Broker()
    pipeline = CopyPipeline(parser, broker)

    pipeline.receive(1)
    pipeline.receive(2)

    assert pipeline.receive(3) == "ambiguous"
    assert broker.order_count == 0


def test_additional_tp_respects_route_policy() -> None:
    parser = FakeCopyParser(
        {
            1: FakeParsedAction(
                "open",
                symbol="EURUSD",
                direction="buy",
                stop_loss=Decimal("1.08"),
            ),
            2: FakeParsedAction(
                "add_tp", symbol="EURUSD", take_profit=Decimal("1.12")
            ),
        }
    )
    disabled_broker = FakeMt5Broker()
    disabled = CopyPipeline(parser, disabled_broker, multiple_tp=False)
    disabled.receive(1)
    assert disabled.receive(2) == "ignored"
    assert disabled_broker.order_count == 1

    enabled_broker = FakeMt5Broker()
    enabled = CopyPipeline(parser, enabled_broker, multiple_tp=True)
    enabled.receive(1)
    assert enabled.receive(2) == "submitted"
    assert enabled_broker.order_count == 2
