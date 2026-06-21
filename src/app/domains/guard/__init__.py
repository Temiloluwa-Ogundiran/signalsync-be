"""Partna Guard — read-only, always-on prop-challenge watchdog.

One deterministic engine, surfaced as read-models. The engine emits a single
AccountState per poll; the watcher persists it and fires email alerts; the API
projects it to the FE. v1 is awareness-only — Guard warns, it never closes a trade.
"""
