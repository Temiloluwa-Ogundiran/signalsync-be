"""Root entry point — run with: uvicorn main:app --reload"""
import sys
from pathlib import Path

# Make `src/` importable when running from the project root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from app.main import app  # noqa: E402, F401

import os
from app.core.config import settings

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", getattr(settings, "PORT", 8000)))

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
