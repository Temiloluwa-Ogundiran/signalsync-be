from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_NAME = "e7f8a9b0c1d2_add_copy_trading_control_plane.py"


def test_copy_trading_revision_is_the_single_head() -> None:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert script.get_current_head() == "f8a9b0c1d2e3"
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
