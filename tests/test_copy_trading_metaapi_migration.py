from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_NAME = "e1f2a3b4c5d6_add_metaapi_copy_connections.py"


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / MIGRATION_NAME
    spec = spec_from_file_location("e1f2a3b4c5d6_add_metaapi_copy_connections", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metaapi_copy_revision_is_the_only_head() -> None:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))

    assert script.get_heads() == ["e1f2a3b4c5d6"]
    revision = script.get_revision("e1f2a3b4c5d6")
    assert revision is not None
    assert revision.down_revision == "d0e1f2a3b4c5"


def test_metaapi_copy_migration_pauses_routes_and_adds_connection_ownership() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic" / "versions" / MIGRATION_NAME).read_text(encoding="utf-8")

    assert '"copy_trading_connections"' in migration
    assert "UPDATE copy_routes" in migration
    assert "state = 'paused'" in migration
    for column in (
        "target_connection_id",
        "connection_id",
        "legacy_account_id",
    ):
        assert column in migration


def test_metaapi_copy_migration_skips_legacy_index_drop_when_index_is_missing(monkeypatch) -> None:
    migration = _load_migration_module()
    dropped_indexes: list[tuple[str, str | None]] = []

    class FakeInspector:
        def get_indexes(self, table_name):
            if table_name != "trade_intents":
                return []
            return [{"name": "ix_trade_intent_account_state"}]

    fake_op = SimpleNamespace(
        get_bind=lambda: object(),
        drop_index=lambda index_name, **kwargs: dropped_indexes.append(
            (index_name, kwargs.get("table_name"))
        ),
    )

    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())

    migration._drop_index_if_exists("trade_intents", "ix_trade_intents_account_id")
    migration._drop_index_if_exists("trade_intents", "ix_trade_intent_account_state")

    assert dropped_indexes == [("ix_trade_intent_account_state", "trade_intents")]
