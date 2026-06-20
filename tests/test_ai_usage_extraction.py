import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app.domains.ai.models  # noqa: F401

from app.domains.ai.router import _read_usage


def test_read_usage_from_dict():
    # LangChain shape — usage_metadata is a dict.
    meta = {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    assert _read_usage(meta) == (12, 5)


def test_read_usage_from_object():
    class M:
        input_tokens = 8
        output_tokens = 3

    assert _read_usage(M()) == (8, 3)


def test_read_usage_handles_none_and_missing():
    assert _read_usage(None) == (0, 0)
    assert _read_usage({}) == (0, 0)
    assert _read_usage({"input_tokens": None, "output_tokens": None}) == (0, 0)
