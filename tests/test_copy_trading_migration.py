from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_NAME = "e7f8a9b0c1d2_add_copy_trading_control_plane.py"


def test_copy_trading_revision_is_in_the_single_head_lineage() -> None:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert len(script.get_heads()) == 1
    head = script.get_revision(script.get_current_head())
    lineage = {revision.revision for revision in script.walk_revisions("base", head.revision)}
    assert "f8a9b0c1d2e3" in lineage
    revision = script.get_revision("e7f8a9b0c1d2")
    assert revision is not None
    assert revision.down_revision == "onboard9f8e7d6c5b4a"


def test_copy_trading_migration_declares_all_control_plane_tables() -> None:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / MIGRATION_NAME
    text = path.read_text(encoding="utf-8")
    for table in (
        "copy_trading_user_settings",
        "telegram_connections",
        "telegram_sources",
        "copy_account_policies",
        "copy_routes",
        "copy_activity_events",
    ):
        assert f'"{table}"' in text


def test_global_telegram_ownership_migration_replaces_user_scoped_constraint() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "c5e6f7a8b9c1_enforce_global_telegram_ownership.py"
    text = path.read_text(encoding="utf-8")

    assert '"telegram_connections_user_id_telegram_user_id_key"' in text
    assert '"uq_telegram_connections_telegram_user_id"' in text
    assert "GROUP BY telegram_user_id" in text
    assert "HAVING COUNT(*) > 1" in text
