from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FakeParsedAction:
    action: str
    symbol: str | None = None
    direction: str | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None


class FakeCopyParser:
    def __init__(self, actions: dict[int, FakeParsedAction]) -> None:
        self.actions = actions

    def parse(self, message_id: int) -> FakeParsedAction:
        return self.actions[message_id]
