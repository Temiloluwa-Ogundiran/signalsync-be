from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REVISION = "d0e1f2a3b4c5"
MIGRATION = "a0b1c2d3e4f5_harden_copy_trading_runtime.py"
GENERATION_MIGRATION = "d0e1f2a3b4c5_add_copy_execution_generations.py"


def test_reliability_revision_is_the_single_head() -> None:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))

    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == "c9d8e7f6a5b4"


def test_reliability_migration_is_additive_and_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "alembic" / "versions" / MIGRATION).read_text(encoding="utf-8")

    for table in (
        "signal_conversations",
        "route_signal_assemblies",
        "telegram_auth_attempts",
        "copy_dead_letters",
        "copy_worker_health",
    ):
        assert f'"{table}"' in text

    for column in (
        "client_order_id",
        "original_volume",
        "current_volume",
        "broker_synced_at",
    ):
        assert f'"{column}"' in text

    assert "drop_table(\"signal_threads\")" not in text


def test_generation_migration_is_additive() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (
        root / "alembic" / "versions" / GENERATION_MIGRATION
    ).read_text(encoding="utf-8")

    for column in (
        "generation",
        "opening_action",
        "opening_intent_id",
        "terminal_reason",
        "completed_at",
    ):
        assert f'"{column}"' in text

    assert 'down_revision = "c9d8e7f6a5b4"' in text
    assert 'drop_table("route_signal_assemblies")' not in text
