"""Credential-free local paper-trading adapter for new Algosark accounts."""

from __future__ import annotations

import zlib
from datetime import datetime

from spsg.backtester import load_price_history
from .base import BrokerAccount, BrokerAdapter, BrokerOrder, BrokerPosition, OrderSide, OrderType


class PaperTradingAdapter(BrokerAdapter):
    provider_name = "algosark-paper"
    STARTING_CASH = 10_000.0
    SUPPORTED_ASSETS = ("AAPL", "TSLA")
    _START_PRICES = {
        "AAPL": 228.0, "BTCUSD": 67_000.0, "EURUSD": 1.09,
        "FTSE100": 8_245.0, "GBPUSD": 1.27, "SPX": 5_487.0,
        "TSLA": 179.0, "USDJPY": 149.9,
    }

    def __init__(self) -> None:
        self._connected = False
        self._cash = self.STARTING_CASH
        self._positions: dict[str, dict[str, float]] = {}
        self._prices: dict[str, list[float]] = {}

    def connect(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        if not paper:
            raise ValueError("The built-in paper account cannot be used for live trading.")
        self._connected = True

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("PaperTradingAdapter.connect() must be called before use")

    def _history(self, symbol: str) -> list[float]:
        symbol = symbol.upper()
        if symbol not in self._prices:
            prices = load_price_history(n_days=500, seed=zlib.crc32(symbol.encode("utf-8")))
            start_price = self._START_PRICES.get(symbol, 100.0)
            scale = start_price / float(prices[0])
            self._prices[symbol] = (prices * scale).tolist()
        return self._prices[symbol]

    def _price(self, symbol: str) -> float:
        return self._history(symbol)[-1]

    def get_account(self) -> BrokerAccount:
        self._ensure_connected()
        equity = self._cash + sum(
            position["qty"] * self._price(symbol)
            for symbol, position in self._positions.items()
        )
        return BrokerAccount("algosark-paper", self._cash, equity, "USD", True)

    def get_positions(self) -> list[BrokerPosition]:
        self._ensure_connected()
        result = []
        for symbol, position in self._positions.items():
            signed_qty = position["qty"]
            if signed_qty == 0:
                continue
            price = self._price(symbol)
            result.append(BrokerPosition(
                symbol=symbol,
                qty=abs(signed_qty),
                side="long" if signed_qty > 0 else "short",
                avg_entry_price=position["avg_entry_price"],
                current_price=price,
                unrealized_pl=(price - position["avg_entry_price"]) * signed_qty,
            ))
        return result

    def get_tradable_assets(self) -> list[dict]:
        self._ensure_connected()
        return [
            {"symbol": symbol, "name": symbol, "exchange": "SIMULATED", "fractionable": True}
            for symbol in self.SUPPORTED_ASSETS
        ]

    def get_tradable_asset(self, symbol: str) -> dict | None:
        normalized = symbol.strip().upper()
        return next(
            (asset for asset in self.get_tradable_assets() if asset["symbol"] == normalized),
            None,
        )

    def place_order(
        self, symbol: str, qty: float, side: OrderSide, order_type: OrderType = "market",
        limit_price: float | None = None, stop_price: float | None = None,
    ) -> BrokerOrder:
        self._ensure_connected()
        if qty <= 0:
            raise ValueError("Order quantity must be greater than zero.")

        symbol = symbol.upper()
        price = self._price(symbol)
        old = self._positions.get(symbol, {"qty": 0.0, "avg_entry_price": 0.0})
        signed_delta = qty if side == "buy" else -qty
        new_qty = old["qty"] + signed_delta
        if old["qty"] == 0 or old["qty"] * signed_delta > 0:
            avg_entry_price = (
                abs(old["qty"]) * old["avg_entry_price"] + qty * price
            ) / (abs(old["qty"]) + qty)
        elif old["qty"] * new_qty < 0:
            avg_entry_price = price
        else:
            avg_entry_price = old["avg_entry_price"]

        self._cash -= signed_delta * price
        if new_qty:
            self._positions[symbol] = {
                "qty": new_qty,
                "avg_entry_price": avg_entry_price,
            }
        else:
            self._positions.pop(symbol, None)

        return BrokerOrder(
            order_id=f"paper-{symbol}-{datetime.utcnow().timestamp()}",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            status="filled",
            filled_avg_price=price,
        )

    def close_position(self, symbol: str) -> BrokerOrder:
        self._ensure_connected()
        symbol = symbol.upper()
        position = self._positions.get(symbol)
        if not position:
            raise ValueError(f"No open paper position for {symbol}.")
        return self.place_order(
            symbol=symbol,
            qty=abs(position["qty"]),
            side="sell" if position["qty"] > 0 else "buy",
        )

    def get_historical_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 500) -> list[dict]:
        self._ensure_connected()
        closes = self._history(symbol)[-limit:]
        seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        step = seconds.get(timeframe, 86400)
        now = datetime.utcnow().timestamp()
        return [
            {
                "t": now - (len(closes) - index) * step,
                "o": close, "h": close * 1.001, "l": close * 0.999, "c": close,
            }
            for index, close in enumerate(closes)
        ]

    def get_latest_quote(self, symbol: str) -> dict:
        self._ensure_connected()
        price = self._price(symbol)
        return {
            "symbol": symbol.upper(),
            "bid": price * 0.9995,
            "ask": price * 1.0005,
            "price": price,
            "timestamp": datetime.utcnow().isoformat(),
        }
