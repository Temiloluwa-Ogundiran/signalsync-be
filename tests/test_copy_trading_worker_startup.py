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
