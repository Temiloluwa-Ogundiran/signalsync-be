from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def _load_script_directory() -> ScriptDirectory:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    return ScriptDirectory.from_config(config)


def test_alembic_upgrade_path_supports_legacy_deployed_revision() -> None:
    script = _load_script_directory()

    legacy_revision = script.get_revision("d9b1e7f4c2a8")
    assert legacy_revision is not None

    head_revision = script.get_current_head()
    upgrade_path = list(script.iterate_revisions(head_revision, "d9b1e7f4c2a8"))

    assert upgrade_path
    assert upgrade_path[-1].down_revision == "d9b1e7f4c2a8"
