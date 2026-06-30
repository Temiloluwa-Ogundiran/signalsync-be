from pathlib import Path

from app.domains.copy_trading.metaapi_execution import is_permanent_metaapi_error


def test_metaapi_error_classification_keeps_ambiguous_transport_failures_uncertain() -> None:
    assert is_permanent_metaapi_error(ValueError("invalid volume")) is True
    assert is_permanent_metaapi_error(TimeoutError("socket timed out")) is False
    assert is_permanent_metaapi_error(ConnectionError("websocket closed")) is False


def test_copy_domain_has_no_self_hosted_mt5_execution_dependency() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "app" / "domains" / "copy_trading"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
        if path.name != "execution.py"
    )

    assert "Mt5CoreClient" not in source
    assert '"/orders"' not in source
    assert '"/account/reconcile"' not in source
