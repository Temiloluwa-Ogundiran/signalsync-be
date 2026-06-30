from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_NAME = "e1f2a3b4c5d6_add_metaapi_copy_connections.py"


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
