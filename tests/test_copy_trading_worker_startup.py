import os
import subprocess
import sys
from pathlib import Path


def test_worker_runtime_registers_complete_model_graph() -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.domains.copy_trading.worker_runtime; "
                "from sqlalchemy.orm import configure_mappers; "
                "configure_mappers()"
            ),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_worker_runtime_has_no_channel_learning_role() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "src" / "app" / "domains" / "copy_trading" / "worker_runtime.py"
    ).read_text(encoding="utf-8")
    startup = (project_root / "scripts" / "docker_start.py").read_text(
        encoding="utf-8"
    )

    assert '"copy-learning"' not in source
    assert "source.learn" not in source
    assert "learning_handler" not in source
    assert '"copy-learning"' not in startup
