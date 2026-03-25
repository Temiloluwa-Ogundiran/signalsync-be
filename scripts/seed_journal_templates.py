import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.core.database import SessionLocal  # noqa: E402
from app.services import startup_service  # noqa: E402


def seed_journal_templates() -> None:
    with SessionLocal() as db:
        result = startup_service.seed_system_journal_templates(db)

    print(
        json.dumps({"status": "ok", **result})
    )


if __name__ == "__main__":
    seed_journal_templates()
