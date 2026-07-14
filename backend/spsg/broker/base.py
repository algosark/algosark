"""
spsg/broker/base.py
====================
Abstract interface every brokerage integration must implement. Keeping this
thin and provider-agnostic is what lets the business plan's "Broker & Data
Integration" roadmap item (Interactive Brokers, Alpaca, "other supported
brokerage platforms") be satisfied by adding new adapters without touching
the strategy-generation or dashboard code.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]


@dataclass
class BrokerAccount:
    account_id: str
    cash: float
    portfolio_value: float
    currency: str
    is_paper: bool


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    side: str          # "long" | "short"
    avg_entry_price: float
    current_price: float
    unrealized_pl: float


@dataclass
class BrokerOrder:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    status: str
    filled_avg_price: float | None = None


class BrokerAdapter(ABC):
    """Every concrete adapter (AlpacaAdapter, IBAdapter, ...) implements this."""

    provider_name: str = "base"

    @abstractmethod
    def connect(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        ...

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        ...

    @abstractmethod
    def place_order(
        self, symbol: str, qty: float, side: OrderSide, order_type: OrderType = "market",
        limit_price: float | None = None, stop_price: float | None = None,
    ) -> BrokerOrder:
        ...

    @abstractmethod
    def close_position(self, symbol: str) -> BrokerOrder:
        ...

    @abstractmethod
    def get_historical_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 500) -> list[dict]:
        """Returns a list of {t, o, h, l, c} bars (oldest first) — feeds
        spsg.backtester.run_backtest / spsg.regime.RegimeClassifier.predict
        (which only need the 'c' closes) and the dashboard's price chart
        (which wants the full OHLC for the timeframe the user selected)."""
        ...

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Returns {symbol, bid, ask, price, timestamp}. `price` is the
        mid-point, used for the dashboard's headline price display."""
        ...
