import os

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app.domains.users.models  # noqa: F401

from app.domains.users import service as user_service
from app.domains.users.schemas import CompleteOnboardingRequest


def test_complete_onboarding_sets_answers_and_flag() -> None:
    db = MagicMock()
    user = MagicMock()
    user.onboarding_completed = False
    user.onboarding_completed_at = None

    payload = CompleteOnboardingRequest(
        trading_experience="1_3y", primary_goal="journal", referral_source="x"
    )
    result = user_service.complete_onboarding(db, current_user=user, payload=payload)

    assert result.onboarding_completed is True
    assert result.onboarding_completed_at is not None
    assert result.trading_experience == "1_3y"
    assert result.primary_goal == "journal"
    assert result.referral_source == "x"
    db.commit.assert_called_once()


def test_onboarding_request_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        CompleteOnboardingRequest(trading_experience="bogus")
    with pytest.raises(ValidationError):
        CompleteOnboardingRequest(primary_goal="bogus")
    with pytest.raises(ValidationError):
        CompleteOnboardingRequest(referral_source="bogus")


def test_onboarding_request_accepts_known_values_and_none() -> None:
    ok = CompleteOnboardingRequest(
        trading_experience="5y_plus", primary_goal="funded", referral_source="community"
    )
    assert ok.primary_goal == "funded"
    # All optional → empty payload is valid.
    assert CompleteOnboardingRequest().trading_experience is None


def test_primary_goal_accepts_multi_select_comma_list() -> None:
    ok = CompleteOnboardingRequest(primary_goal="journal,analyze,backtest")
    assert ok.primary_goal == "journal,analyze,backtest"
    # Whitespace is trimmed.
    assert CompleteOnboardingRequest(primary_goal=" journal , funded ").primary_goal == "journal,funded"


def test_primary_goal_rejects_unknown_token_in_list() -> None:
    with pytest.raises(ValidationError):
        CompleteOnboardingRequest(primary_goal="journal,bogus")
