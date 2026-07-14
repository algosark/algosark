"""
broker_registry.py
====================
In-memory registry of connected broker adapters, keyed by (user_id, mode)
where mode is "paper" or "live". Keying by mode (not just user_id) is what
lets a user have both a paper AND a live Alpaca connection active at once,
so the dashboard's paper/live toggle can switch instantly without
reconnecting — each mode has its own adapter instance (Alpaca paper and live
trading use separate API key pairs, so this mirrors that reality).

Shared between api_strategy.py's broker router and api_trading.py so both
modules see the same connections. This is intentionally the simplest thing
that works for an MVP — see the "not production-ready" note below.

NOT PRODUCTION READY: this dict lives in one process's memory. It's wiped on
every restart/deploy, and won't work at all once you run more than one
uvicorn worker (each worker gets its own empty dict). Before going live,
replace this with encrypted-at-rest credential storage (e.g. a `broker_credentials`
table with a KMS-encrypted secret column), re-hydrating an adapter per
request rather than caching the connection itself.
"""

from __future__ import annotations
from spsg.broker import AlpacaAdapter

_adapters: dict[tuple[int, str], AlpacaAdapter] = {}


def mode_key(paper: bool) -> str:
    return "paper" if paper else "live"


def set_adapter(user_id: int, paper: bool, adapter: AlpacaAdapter) -> None:
    _adapters[(user_id, mode_key(paper))] = adapter


def get_adapter(user_id: int, mode: str) -> AlpacaAdapter | None:
    return _adapters.get((user_id, mode))


def clear_adapter(user_id: int, mode: str) -> None:
    _adapters.pop((user_id, mode), None)


def connected_modes(user_id: int) -> list[str]:
    return [m for (uid, m) in _adapters.keys() if uid == user_id]
