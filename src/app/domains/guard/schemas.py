"""Pydantic request/response schemas for the Guard API.

The rule-spec input mirrors the engine's FirmRuleSpec, with percentages entered as
fractions (the FE form converts whole-number % on the way in). Response shapes are
the engine's read-models (monitor/copilot/rules) plus chart + alert data.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import (
    Basis,
    ConsistencyBasis,
    DailyAnchor,
    DailyType,
    DrawdownAnchorRef,
    DrawdownType,
    TradingDayRule,
)


# -- rule spec input ----------------------------------------------------------

class DailyLossInput(BaseModel):
    pct: Decimal = Field(gt=0, lt=1)            # fraction, e.g. 0.05
    basis: Basis = Basis.EQUITY
    type: DailyType = DailyType.STATIC
    anchor: DailyAnchor = DailyAnchor.DAY_START_BALANCE
    reset_hour: int = Field(default=0, ge=0, le=23)
    reset_minute: int = Field(default=0, ge=0, le=59)
    reset_tz: str = Field(default="UTC", max_length=64)
    # Optional soft-breach: fraction of the daily allowance consumed that pauses
    # the day (e.g. 0.5 = E8's 2% pause inside a 4% limit). 0 disables it.
    soft_pct: Decimal = Field(default=Decimal(0), ge=0, lt=1)


class MaxDrawdownInput(BaseModel):
    pct: Decimal = Field(gt=0, lt=1)
    type: DrawdownType = DrawdownType.STATIC
    anchor_ref: DrawdownAnchorRef = DrawdownAnchorRef.INITIAL_BALANCE
    locks_at_initial: bool = False


class ProfitTargetInput(BaseModel):
    pct: Decimal = Field(gt=0, lt=1)


class MinDaysInput(BaseModel):
    count: int = Field(ge=0, le=60)
    day_counts_if: TradingDayRule = TradingDayRule.ANY_TRADE
    moves_pct: Decimal = Field(default=Decimal(0), ge=0, lt=1)


class ConsistencyInput(BaseModel):
    cap: Decimal = Field(gt=0, le=1)
    basis: ConsistencyBasis = ConsistencyBasis.TOTAL_PROFIT


class RuleSpecInput(BaseModel):
    firm: str = Field(default="user", max_length=120)
    daily_loss: DailyLossInput
    max_drawdown: MaxDrawdownInput
    profit_target: ProfitTargetInput
    min_days: Optional[MinDaysInput] = None
    consistency: Optional[ConsistencyInput] = None


class PersonalInput(BaseModel):
    daily_frac: Decimal = Field(default=Decimal(1), ge=Decimal("0.10"), le=1)
    dd_frac: Decimal = Field(default=Decimal(1), ge=Decimal("0.10"), le=1)


# -- create / update ----------------------------------------------------------

class GuardEnableRequest(BaseModel):
    trading_account_id: uuid.UUID
    size: Decimal = Field(gt=0)
    rule_spec: RuleSpecInput
    personal: Optional[PersonalInput] = None
    contract_text: Optional[str] = Field(default=None, max_length=2000)


class GuardUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    size: Optional[Decimal] = Field(default=None, gt=0)
    rule_spec: Optional[RuleSpecInput] = None
    personal: Optional[PersonalInput] = None
    contract_text: Optional[str] = Field(default=None, max_length=2000)


# -- responses ----------------------------------------------------------------

class GuardAccountResponse(BaseModel):
    id: uuid.UUID
    trading_account_id: uuid.UUID
    enabled: bool
    size: Decimal
    connection_health: str
    last_polled_at: Optional[datetime] = None
    status: Optional[str] = None  # latest snapshot status if present
    display_name: Optional[str] = None  # from the linked TradingAccount
    broker_name: Optional[str] = None   # from the linked TradingAccount
    # The hydrated config so the switcher + rules form render without extra calls.
    rule_spec: dict[str, Any]
    personal: Optional[dict[str, Any]] = None
    contract_text: Optional[str] = None


# The /monitor and /rules endpoints return flat dicts assembled in the service
# (the engine read-models), not Pydantic-typed shells — see service.get_monitor /
# get_rules and the FE's GuardMonitor / GuardRulesView types.
