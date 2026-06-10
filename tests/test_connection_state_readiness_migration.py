from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "c2f4a8d6e9b1_add_connection_state_readiness_fields.py"
    )
    spec = spec_from_file_location(
        "c2f4a8d6e9b1_add_connection_state_readiness_fields",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_connection_state_readiness_migration_skips_columns_that_already_exist(
    monkeypatch,
):
    migration = _load_migration_module()
    recorded_columns: list[str] = []
    altered_columns: list[str] = []

    existing_columns = {
        "connection_state",
        "is_data_ready_for_stats",
        "last_bootstrap_synced_at",
        "bootstrap_error_message",
    }

    class FakeInspector:
        def get_columns(self, table_name):
            if table_name != "trading_accounts":
                return []
            return [{"name": name} for name in existing_columns]

    fake_op = SimpleNamespace(
        execute=lambda *_args, **_kwargs: None,
        get_bind=lambda: object(),
        add_column=lambda _table_name, column: recorded_columns.append(column.name),
        alter_column=lambda _table_name, column_name, **_kwargs: altered_columns.append(column_name),
    )

    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())

    migration.upgrade()

    assert recorded_columns == []
    assert altered_columns == ["connection_state", "is_data_ready_for_stats"]
