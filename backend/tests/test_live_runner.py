"""
Tests spsg.live_runner against a fully in-memory mock broker (no network),
covering: opening a fresh position, holding when signal agrees, flipping
sides when the signal reverses, and the drawdown circuit breaker.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from spsg.broker.base import BrokerAdapter, BrokerAccount, BrokerPosition, BrokerOrder
from spsg.live_runner import run_strategy_once, STATE_DIR

# Ensure a clean slate — a stale equity-peak file from a previous run would
# make Test 1 trip the drawdown circuit breaker before it even starts.
import shutil
if STATE_DIR.exists():
    shutil.rmtree(STATE_DIR)


class MockAdapter(BrokerAdapter):
    provider_name = "mock"

    def __init__(self, portfolio_value: float, bars: dict[str, list[float]], positions: list[BrokerPosition] | None = None):
        self.portfolio_value = portfolio_value
        self.bars = bars
        self._positions = {p.symbol: p for p in (positions or [])}
        self.calls = []

    def connect(self, *a, **kw): pass

    def get_account(self) -> BrokerAccount:
        return BrokerAccount("mock-1", cash=self.portfolio_value * 0.3, portfolio_value=self.portfolio_value,
                              currency="USD", is_paper=True)

    def get_positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    def place_order(self, symbol, qty, side, order_type="market", limit_price=None, stop_price=None) -> BrokerOrder:
        self.calls.append(("place_order", symbol, qty, side))
        self._positions[symbol] = BrokerPosition(
            symbol=symbol, qty=qty, side="long" if side == "buy" else "short",
            avg_entry_price=self.bars[symbol][-1], current_price=self.bars[symbol][-1], unrealized_pl=0.0,
        )
        return BrokerOrder(order_id="o1", symbol=symbol, side=side, qty=qty, order_type=order_type, status="filled")

    def close_position(self, symbol) -> BrokerOrder:
        self.calls.append(("close_position", symbol))
        self._positions.pop(symbol, None)
        return BrokerOrder(order_id="o2", symbol=symbol, side="sell", qty=0, order_type="market", status="closed")

    def get_historical_bars(self, symbol, timeframe="1Day", limit=500) -> list[dict]:
        closes = self.bars[symbol][-limit:]
        return [{"t": str(i), "o": c, "h": c, "l": c, "c": c} for i, c in enumerate(closes)]

    def get_latest_quote(self, symbol) -> dict:
        price = self.bars[symbol][-1]
        return {"symbol": symbol, "bid": price - 0.01, "ask": price + 0.01, "price": price, "timestamp": None}


strategy = {
    "parameters": {
        "risk_per_trade_pct": 2.0,
        "leverage": "Up to 2.0:1",
        "max_drawdown_pct": 20.0,
        "holding_period_days": 8,
        "max_positions": 3,
    }
}

# Uptrend bars -> current signal should be long (fast MA > slow MA)
uptrend = list(100 * np.exp(np.cumsum(np.full(120, 0.004))))
downtrend = list(100 * np.exp(np.cumsum(np.full(120, -0.004))))

print("=== Test 1: fresh long entry on uptrend ===")
adapter = MockAdapter(portfolio_value=10000, bars={"AAPL": uptrend})
results = run_strategy_once(adapter, user_id=999, strategy=strategy, symbols=["AAPL"])
print(results)
assert results[0].action_taken == "opened"
assert results[0].target_position == "long"

print("\n=== Test 2: already long + still uptrend -> no action ===")
adapter2 = MockAdapter(portfolio_value=10000, bars={"AAPL": uptrend},
                        positions=[__import__("spsg.broker.base", fromlist=["BrokerPosition"]).BrokerPosition(
                            "AAPL", 10, "long", 100, uptrend[-1], 50)])
results2 = run_strategy_once(adapter2, user_id=999, strategy=strategy, symbols=["AAPL"])
print(results2)
assert results2[0].action_taken == "none"

print("\n=== Test 3: currently long, trend reversed to downtrend -> flip to short ===")
from spsg.broker.base import BrokerPosition
adapter3 = MockAdapter(portfolio_value=10000, bars={"AAPL": downtrend},
                        positions=[BrokerPosition("AAPL", 10, "long", 100, downtrend[-1], -50)])
results3 = run_strategy_once(adapter3, user_id=999, strategy=strategy, symbols=["AAPL"])
print(results3, adapter3.calls)
assert results3[0].action_taken == "flipped"
assert ("close_position", "AAPL") in adapter3.calls

print("\n=== Test 4: drawdown circuit breaker flattens everything ===")
# Force a bad peak into state file, then run with a much lower current equity
import json
STATE_DIR.mkdir(parents=True, exist_ok=True)
(STATE_DIR / "user_999_equity_peak.json").write_text(json.dumps({"peak_equity": 20000}))
adapter4 = MockAdapter(portfolio_value=10000, bars={"AAPL": uptrend},  # -50% drawdown vs peak
                        positions=[BrokerPosition("AAPL", 10, "long", 100, uptrend[-1], 50)])
results4 = run_strategy_once(adapter4, user_id=999, strategy=strategy, symbols=["AAPL"])
print(results4)
assert results4[0].action_taken == "circuit_breaker_flatten"

print("\nALL LIVE RUNNER TESTS PASSED")
