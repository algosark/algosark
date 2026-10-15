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
import os
from spsg.broker import BrokerAdapter
from spsg.broker import AlpacaAdapter
from spsg.broker.paper_adapter import PaperTradingAdapter

_adapters: dict[tuple[int, str], BrokerAdapter] = {}
_shared_paper_adapter: BrokerAdapter | None = None


def _get_shared_paper_adapter() -> BrokerAdapter:
    global _shared_paper_adapter
    if _shared_paper_adapter is None:
        api_key = os.getenv("ALPACA_KEY") or os.getenv("ALPACA_API_KEY")
        api_secret = os.getenv("ALPACA_SECRET") or os.getenv("ALPACA_API_SECRET")
        if api_key and api_secret:
            if os.getenv("ALPACA_PAPER", "true").lower() != "true":
                raise RuntimeError("The shared Algosark Alpaca account must be configured for paper trading.")
            adapter = AlpacaAdapter()
            adapter.connect(api_key, api_secret, paper=True)
            _shared_paper_adapter = adapter
        else:
            adapter = PaperTradingAdapter()
            adapter.connect("", "", paper=True)
            _shared_paper_adapter = adapter
    return _shared_paper_adapter


def mode_key(paper: bool) -> str:
    return "paper" if paper else "live"


def set_adapter(user_id: int, paper: bool, adapter: BrokerAdapter) -> None:
    _adapters[(user_id, mode_key(paper))] = adapter


def get_adapter(user_id: int, mode: str) -> BrokerAdapter | None:
    return _adapters.get((user_id, mode))


def ensure_paper_adapter(user_id: int) -> BrokerAdapter:
    adapter = get_adapter(user_id, "paper")
    if adapter is None:
        adapter = _get_shared_paper_adapter()
        set_adapter(user_id, True, adapter)
    return adapter


def clear_adapter(user_id: int, mode: str) -> None:
    _adapters.pop((user_id, mode), None)


def connected_modes(user_id: int) -> list[str]:
    return [m for (uid, m) in _adapters.keys() if uid == user_id]
