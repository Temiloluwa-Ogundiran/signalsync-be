from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "d1e2f3a4b5c6_add_journal_reviewed_at_columns.py"
    )
    spec = spec_from_file_location(
        "d1e2f3a4b5c6_add_journal_reviewed_at_columns",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_journal_reviewed_at_migration_skips_columns_that_already_exist(
    monkeypatch,
):
    migration = _load_migration_module()
    recorded_columns: list[tuple[str, str]] = []

    existing_columns = {
        "daily_journals": {"reviewed_at"},
        "trade_journals": {"reviewed_at"},
    }

    class FakeInspector:
        def get_columns(self, table_name):
            return [{"name": name} for name in existing_columns.get(table_name, set())]

    fake_op = SimpleNamespace(
        get_bind=lambda: object(),
        add_column=lambda table_name, column: recorded_columns.append(
            (table_name, column.name)
        ),
    )

    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())

    migration.upgrade()

    assert recorded_columns == []
