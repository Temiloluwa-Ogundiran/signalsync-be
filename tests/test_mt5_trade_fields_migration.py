from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "a1b2c3d4e5f6_add_mt5_trade_fields.py"
    )
    spec = spec_from_file_location("a1b2c3d4e5f6_add_mt5_trade_fields", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mt5_trade_fields_migration_skips_columns_and_indexes_that_already_exist(monkeypatch):
    migration = _load_migration_module()
    recorded_columns: list[tuple[str, str]] = []
    created_indexes: list[str] = []

    existing_columns = {
        "trades": {
            "sl",
            "tp",
            "magic_number",
            "position_id",
            "trade_source",
            "mfe",
            "mae",
        },
        "trading_accounts": {
            "sync_provider",
            "copy_magic_numbers",
        },
    }
    existing_indexes = {
        "trades": {"ix_trades_position_id"},
    }

    class FakeInspector:
        def get_columns(self, table_name):
            return [{"name": name} for name in existing_columns.get(table_name, set())]

        def get_indexes(self, table_name):
            return [{"name": name} for name in existing_indexes.get(table_name, set())]

    fake_op = SimpleNamespace(
        execute=lambda *_args, **_kwargs: None,
        get_bind=lambda: object(),
        add_column=lambda table_name, column: recorded_columns.append((table_name, column.name)),
        create_index=lambda index_name, *_args, **_kwargs: created_indexes.append(index_name),
    )

    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())

    migration.upgrade()

    assert recorded_columns == []
    assert created_indexes == []
