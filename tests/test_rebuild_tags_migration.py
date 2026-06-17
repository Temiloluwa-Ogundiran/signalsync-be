from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "f7a8b9c0d1e2_rebuild_tags_groups_tags.py"
    )
    spec = spec_from_file_location("f7a8b9c0d1e2_rebuild_tags_groups_tags", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_fake_op(existing_tables, created_tables, dropped_tables, created_indexes):
    return SimpleNamespace(
        f=lambda value: value,
        get_bind=lambda: object(),
        create_table=lambda table_name, *a, **k: created_tables.append(table_name),
        drop_table=lambda table_name, *a, **k: dropped_tables.append(table_name),
        create_index=lambda index_name, *a, **k: created_indexes.append(index_name),
        drop_index=lambda index_name, *a, **k: None,
    )


def _patch(monkeypatch, migration, existing_tables, **op_lists):
    class FakeInspector:
        def get_table_names(self):
            return list(existing_tables)

    monkeypatch.setattr(migration, "op", _make_fake_op(existing_tables, **op_lists))
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: FakeInspector())


def test_upgrade_drops_legacy_and_creates_new(monkeypatch):
    migration = _load_migration_module()
    created_tables: list[str] = []
    dropped_tables: list[str] = []
    created_indexes: list[str] = []

    existing = {"tag_categories", "tag_options", "trade_tag_selections", "trades", "users"}
    _patch(
        monkeypatch, migration, existing,
        created_tables=created_tables, dropped_tables=dropped_tables, created_indexes=created_indexes,
    )

    migration.upgrade()

    # legacy tables dropped children-first
    assert dropped_tables == ["trade_tag_selections", "tag_options", "tag_categories"]
    # new tables created
    assert created_tables == ["tag_groups", "tags", "trade_tags"]
    assert "ix_tag_groups_user_id" in created_indexes
    assert "ix_tags_group_id" in created_indexes


def test_upgrade_is_idempotent_when_new_tables_exist(monkeypatch):
    migration = _load_migration_module()
    created_tables: list[str] = []
    dropped_tables: list[str] = []
    created_indexes: list[str] = []

    existing = {"tag_groups", "tags", "trade_tags", "trades", "users"}
    _patch(
        monkeypatch, migration, existing,
        created_tables=created_tables, dropped_tables=dropped_tables, created_indexes=created_indexes,
    )

    migration.upgrade()

    # nothing legacy to drop, nothing new to create
    assert dropped_tables == []
    assert created_tables == []
    assert created_indexes == []
