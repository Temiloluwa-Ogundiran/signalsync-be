import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.domains.ai.service import _titler


def test_generate_title_returns_none_for_empty_input() -> None:
    assert _titler.generate_title("") is None
    assert _titler.generate_title("   ") is None


def test_generate_title_returns_none_when_unconfigured() -> None:
    # No API key -> no LLM -> graceful None (caller keeps placeholder title).
    with patch.object(_titler, "_get_llm", return_value=None):
        assert _titler.generate_title("why did I lose on EURUSD") is None


def test_generate_title_uses_llm_and_strips_quotes() -> None:
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content='"EURUSD Losses Review"')
    with patch.object(_titler, "_get_llm", return_value=fake_llm):
        title = _titler.generate_title("why did i lose so much on EURUSD last week")
    assert title == "EURUSD Losses Review"
    fake_llm.invoke.assert_called_once()


def test_generate_title_swallows_llm_errors() -> None:
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = RuntimeError("model down")
    with patch.object(_titler, "_get_llm", return_value=fake_llm):
        assert _titler.generate_title("anything") is None
