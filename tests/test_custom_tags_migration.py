from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "2d629be79bc7_add_custom_tags.py"
    )
    spec = spec_from_file_location(
        "2d629be79bc7_add_custom_tags",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_custom_tags_migration_skips_existing_tables_and_indexes(monkeypatch):
    migration = _load_migration_module()
    created_tables: list[str] = []
    created_indexes: list[str] = []
    dropped_indexes: list[str] = []
    altered_columns: list[tuple[str, str]] = []

    existing_tables = {
        "tag_categories",
        "tag_options",
        "trade_tag_selections",
        "trades",
    }
    existing_indexes = {
        "tag_categories": {"ix_tag_categories_user_id"},
        "tag_options": {"ix_tag_options_category_id", "ix_tag_options_user_id"},
        "trades": set(),
    }

    class FakeInspector:
        def get_table_names(self):
            return list(existing_tables)

        def get_indexes(self, table_name):
            return [{"name": name} for name in existing_indexes.get(table_name, set())]

    fake_op = SimpleNamespace(
        f=lambda value: value,
        get_bind=lambda: object(),
        create_table=lambda table_name, *args, **kwargs: created_tables.append(table_name),
        create_index=lambda index_name, *args, **kwargs: created_indexes.append(index_name),
        drop_index=lambda index_name, *args, **kwargs: dropped_indexes.append(index_name),
        alter_column=lambda table_name, column_name, **kwargs: altered_columns.append((table_name, column_name)),
    )

    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())

    migration.upgrade()

    assert created_tables == []
    assert created_indexes == []
    assert dropped_indexes == []
    assert altered_columns == [
        ("trades", "sl"),
        ("trades", "tp"),
        ("trades", "magic_number"),
        ("trades", "position_id"),
        ("trades", "trade_source"),
        ("trades", "mfe"),
        ("trades", "mae"),
        ("trading_accounts", "sync_provider"),
        ("trading_accounts", "copy_magic_numbers"),
    ]
