"""
Lazy-initialized Supabase client.

Usage (in services when you need Supabase Auth or Storage):

    from app.core.supabase import get_supabase

    client = get_supabase()
    client.storage.from_("bucket").upload(...)
"""

from __future__ import annotations

from typing import Optional

import httpx
from supabase import Client, ClientOptions, create_client

from app.core.config import settings

_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """Return a singleton Supabase client, initializing it on first call."""
    global _supabase_client
    if _supabase_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment."
            )
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        http_client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                connect=10.0,   # time to establish connection
                read=30.0,      # time to wait for a response
                write=60.0,     # time to upload file bytes — needs to be generous
                pool=10.0,      # time to acquire a connection from the pool
            ),
        )
        options = ClientOptions(httpx_client=http_client)
        _supabase_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
            options=options,
        )
    return _supabase_client
