"""
spsg/broker/alpaca_adapter.py
==============================
Alpaca implementation of BrokerAdapter, using plain REST calls (no SDK
dependency, so this stays lightweight and easy to audit for compliance
review). Works against both paper-api.alpaca.markets (paper trading — this is
what's referenced by "Live Trading via Broker API" in Step 5 of the
Operational Model / the dashboard's Trade view) and api.alpaca.markets (live).

Note on this environment: outbound network access here is restricted to a
small allow-list of package registries (pypi/npm/github), so this adapter
cannot actually be exercised against the real Alpaca API from within this
sandbox. It is written and structured to be dropped into your FastAPI backend
as-is; test it there with real (or Alpaca's free paper) API keys.
"""

from __future__ import annotations
import os
import requests

from .base import BrokerAdapter, BrokerAccount, BrokerPosition, BrokerOrder, OrderSide, OrderType


class AlpacaAdapter(BrokerAdapter):
    provider_name = "alpaca"

    PAPER_BASE_URL = "https://paper-api.alpaca.markets"
    LIVE_BASE_URL = "https://api.alpaca.markets"
    DATA_BASE_URL = "https://data.alpaca.markets"

    def __init__(self):
        self._api_key: str | None = None
        self._api_secret: str | None = None
        self._base_url: str | None = None
        self._is_paper: bool = True

    def connect(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._is_paper = paper
        self._base_url = self.PAPER_BASE_URL if paper else self.LIVE_BASE_URL

    def _headers(self) -> dict:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("AlpacaAdapter.connect() must be called before use")
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
        }

    def _get(self, path: str, base: str | None = None, params: dict | None = None) -> dict:
        url = f"{base or self._base_url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self._base_url}{path}"
        resp = requests.post(url, headers=self._headers(), json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        resp = requests.delete(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    # ------------------------------------------------------------------
    def get_account(self) -> BrokerAccount:
        data = self._get("/v2/account")
        return BrokerAccount(
            account_id=data["id"],
            cash=float(data["cash"]),
            portfolio_value=float(data["portfolio_value"]),
            currency=data.get("currency", "USD"),
            is_paper=self._is_paper,
        )

    def get_positions(self) -> list[BrokerPosition]:
        data = self._get("/v2/positions")
        return [
            BrokerPosition(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                side=p["side"],
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
                unrealized_pl=float(p["unrealized_pl"]),
            )
            for p in data
        ]

    def place_order(
        self, symbol: str, qty: float, side: OrderSide, order_type: OrderType = "market",
        limit_price: float | None = None, stop_price: float | None = None,
    ) -> BrokerOrder:
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        if order_type == "limit" and limit_price is not None:
            body["limit_price"] = str(limit_price)
        if order_type == "stop" and stop_price is not None:
            body["stop_price"] = str(stop_price)

        data = self._post("/v2/orders", body)
        return BrokerOrder(
            order_id=data["id"],
            symbol=data["symbol"],
            side=data["side"],
            qty=float(data["qty"]),
            order_type=data["type"],
            status=data["status"],
            filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
        )

    def close_position(self, symbol: str) -> BrokerOrder:
        data = self._delete(f"/v2/positions/{symbol}")
        return BrokerOrder(
            order_id=data.get("id", ""),
            symbol=symbol,
            side="sell",
            qty=float(data.get("qty", 0)),
            order_type="market",
            status=data.get("status", "closed"),
        )

    # Maps the dashboard's timeframe buttons to Alpaca's bar timeframe strings.
    # Alpaca's v2 bars API accepts freeform "<N><unit>" strings for intraday
    # (Min/Hour) and "1Day" for daily. 4Hour bars aren't a native Alpaca
    # timeframe on all plans — if your account rejects it, fall back to
    # requesting "1Hour" and aggregating 4 bars client-side.
    TIMEFRAME_MAP = {
        "1m": "1Min", "5m": "5Min", "15m": "15Min",
        "1h": "1Hour", "4h": "4Hour", "1d": "1Day",
    }

    def get_historical_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 500) -> list[dict]:
        alpaca_tf = self.TIMEFRAME_MAP.get(timeframe, timeframe)
        data = self._get(
            f"/v2/stocks/{symbol}/bars",
            base=self.DATA_BASE_URL,
            params={"timeframe": alpaca_tf, "limit": limit},
        )
        bars = data.get("bars", [])
        return [
            {"t": b["t"], "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]), "c": float(b["c"])}
            for b in bars
        ]

    def get_latest_quote(self, symbol: str) -> dict:
        data = self._get(f"/v2/stocks/{symbol}/quotes/latest", base=self.DATA_BASE_URL)
        quote = data.get("quote", {})
        bid = float(quote.get("bp", 0) or 0)
        ask = float(quote.get("ap", 0) or 0)
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "price": (bid + ask) / 2 if (bid and ask) else (bid or ask),
            "timestamp": quote.get("t"),
        }


def get_adapter_from_env() -> AlpacaAdapter:
    """Convenience factory reading ALPACA_API_KEY / ALPACA_API_SECRET /
    ALPACA_PAPER env vars — wire this into api_strategy.py's broker endpoints."""
    adapter = AlpacaAdapter()
    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_API_SECRET", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
    if api_key and api_secret:
        adapter.connect(api_key, api_secret, paper=paper)
    return adapter
