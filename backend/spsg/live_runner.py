"""
spsg/live_runner.py
=====================
Turns a generated strategy (spsg.strategy_generator output) into actual order
placement, on a schedule. This is the piece that closes the loop between
"strategy generated" and "trades happening" — everything upstream of this
(features -> regime -> ensemble -> backtest) only ever *proposes* a strategy;
this module is what *acts* on it.

Designed to be called periodically (cron / APScheduler / systemd timer — see
scripts/run_live_strategy.py and HOW_TO_RUN_A_STRATEGY.md for scheduling
options), not run as a long-lived process. Each call is stateless except for
a small on-disk equity-peak file used for the drawdown circuit breaker.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import numpy as np

from .backtester import compute_current_signal
from .broker.base import BrokerAdapter

logger = logging.getLogger("spsg.live_runner")

STATE_DIR = Path(__file__).resolve().parent.parent / "runtime_state"


@dataclass
class RunResult:
    symbol: str
    signal: int
    target_position: str          # "long" | "short" | "flat"
    action_taken: str              # "none" | "opened" | "closed" | "flipped" | "circuit_breaker_flatten"
    qty: float
    notes: str = ""


def _state_path(user_id: int) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"user_{user_id}_equity_peak.json"


def _load_peak_equity(user_id: int, current_equity: float) -> float:
    path = _state_path(user_id)
    if path.exists():
        try:
            peak = json.loads(path.read_text())["peak_equity"]
            return max(peak, current_equity)
        except (json.JSONDecodeError, KeyError):
            pass
    return current_equity


def _save_peak_equity(user_id: int, peak_equity: float) -> None:
    _state_path(user_id).write_text(json.dumps({"peak_equity": peak_equity}))


def _target_qty(notional: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return round(notional / price, 4)


def run_strategy_once(
    adapter: BrokerAdapter,
    user_id: int,
    strategy: dict,
    symbols: list[str],
) -> list[RunResult]:
    """
    strategy: the dict returned by SPSGEngine.generate_strategy() / stored in
        the Strategy table's `payload` column.
    symbols: broker-tradeable tickers to apply this strategy to (e.g.
        ["SPY", "QQQ"] for a US-equities profile, or your broker's forex
        symbol convention for an FX profile). The strategy itself doesn't
        pick tickers — SPSG personalises *risk parameters*, not security
        selection — so the caller decides which instruments within the
        strategy's target markets to actually trade. A simple default is to
        pick the most liquid 1-3 instruments per selected market.
    """
    params = strategy["parameters"]
    risk_per_trade_pct = params["risk_per_trade_pct"]
    leverage = float(str(params["leverage"]).split("x")[0].replace("Up to ", "").replace(":1", ""))
    max_drawdown_pct = params["max_drawdown_pct"]
    holding_period_days = params["holding_period_days"]
    max_positions = params["max_positions"]

    account = adapter.get_account()
    positions = {p.symbol: p for p in adapter.get_positions()}

    # ---- Circuit breaker: flatten everything if account drawdown breaches
    # the strategy's max_drawdown_target, mirroring backtester.run_backtest's
    # in-sample circuit breaker so live behaviour matches what was backtested.
    peak_equity = _load_peak_equity(user_id, account.portfolio_value)
    drawdown_pct = (account.portfolio_value - peak_equity) / peak_equity * 100 if peak_equity else 0.0
    _save_peak_equity(user_id, max(peak_equity, account.portfolio_value))

    results: list[RunResult] = []

    if drawdown_pct <= -abs(max_drawdown_pct):
        logger.warning(
            "user=%s drawdown %.2f%% breached max_drawdown_target %.2f%% — flattening all positions",
            user_id, drawdown_pct, max_drawdown_pct,
        )
        for sym in positions:
            adapter.close_position(sym)
            results.append(RunResult(sym, 0, "flat", "circuit_breaker_flatten", 0.0,
                                      notes=f"drawdown {drawdown_pct:.2f}% <= -{max_drawdown_pct}%"))
        return results

    # ---- Per-symbol risk budget: split risk_per_trade across up to
    # max_positions concurrent symbols so aggregate risk matches the strategy.
    symbols = symbols[:max_positions]
    per_symbol_notional = account.portfolio_value * (risk_per_trade_pct / 100.0) * leverage

    for symbol in symbols:
        try:
            bars_data = adapter.get_historical_bars(symbol, timeframe="1Day", limit=200)
            bars = [b["c"] for b in bars_data]
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch bars for %s: %s", symbol, exc)
            results.append(RunResult(symbol, 0, "flat", "none", 0.0, notes=f"bar fetch failed: {exc}"))
            continue

        if len(bars) < 20:
            results.append(RunResult(symbol, 0, "flat", "none", 0.0, notes="insufficient bar history"))
            continue

        sig = compute_current_signal(np.array(bars), holding_period_days)
        signal = sig["signal"]
        target = {1: "long", -1: "short", 0: "flat"}[signal]

        current_pos = positions.get(symbol)
        current_side = current_pos.side if current_pos else "flat"
        price = bars[-1]
        target_qty = _target_qty(per_symbol_notional, price)

        if target == "flat":
            if current_pos:
                adapter.close_position(symbol)
                results.append(RunResult(symbol, signal, target, "closed", 0.0))
            else:
                results.append(RunResult(symbol, signal, target, "none", 0.0))
            continue

        if current_side == target:
            # Already positioned correctly; in a fuller implementation you'd
            # also check/rebalance qty here if account equity moved a lot.
            results.append(RunResult(symbol, signal, target, "none", current_pos.qty if current_pos else 0.0))
            continue

        if current_pos:
            adapter.close_position(symbol)  # flip: close the wrong-way position first

        order_side = "buy" if target == "long" else "sell"
        adapter.place_order(symbol=symbol, qty=target_qty, side=order_side, order_type="market")
        action = "flipped" if current_pos else "opened"
        results.append(RunResult(symbol, signal, target, action, target_qty))

    return results
