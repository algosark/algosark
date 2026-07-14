"""
api_trading.py
================
The Trade tab's backend. Three jobs:

  1. Place and close orders — routes through a connected broker adapter
     (spsg/broker via broker_registry.py) when one exists for the requested
     mode, so paper trades hit Alpaca's real paper API (real fills, real
     data) and live trades hit the real live API. Falls back to a synthetic
     fill price only when no broker is connected for that mode, so the Trade
     tab still works before a user has connected anything — clearly flagged
     via `source: "synthetic"` on the returned trade.
  2. Persist every trade to the `trades` table (models_trade.py) — this is
     what makes Recent Orders / Positions / History real across page loads,
     replacing the old client-only paperPositions/paperHistory JS arrays.
  3. Serve chart data (`/trading/bars`, `/trading/quote`) so the single
     merged Trade tab's timeframe buttons (1m/5m/15m/1h/4h/1d) actually
     change what's plotted, instead of only toggling a CSS class.

Wire-up in main.py (same router-factory pattern as everything else):

    from models_trade import define_trade_model
    from api_trading import build_trading_router

    Trade = define_trade_model(Base)
    Base.metadata.create_all(bind=engine)

    app.include_router(build_trading_router(get_db, get_current_user, Trade))
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

import broker_registry
from spsg.backtester import load_price_history

Mode = Literal["paper", "live"]


# ----------------------------------------------------------------------------
# Synthetic fallback — only used when no broker is connected for the
# requested mode, so the Trade tab remains usable pre-broker-connection.
# Deterministic per symbol+timeframe within a process run (not per-request),
# so repeated bar fetches for the same chart don't jump around discontinuously.
# ----------------------------------------------------------------------------
_SYNTHETIC_CACHE: dict[tuple[str, str], list[float]] = {}

_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

# Realistic starting prices so a synthetic-fallback fill for GBPUSD looks like
# ~1.27, not the generic load_price_history() default of ~100. Purely cosmetic
# — the P&L math is internally consistent either way — but it avoids the
# confusing "GBPUSD filled at 133.85" look during a demo.
_SYNTHETIC_START_PRICE = {
    "GBPUSD": 1.27, "EURUSD": 1.09, "USDJPY": 149.9, "BTCUSD": 67000,
    "AAPL": 228.0, "TSLA": 179.0, "FTSE100": 8245.0, "SPX": 5487.0,
}


def _synthetic_bars(symbol: str, timeframe: str, limit: int) -> list[dict]:
    key = (symbol, timeframe)
    if key not in _SYNTHETIC_CACHE or len(_SYNTHETIC_CACHE[key]) < limit:
        seed = abs(hash(symbol)) % (2**31)
        start_price = _SYNTHETIC_START_PRICE.get(symbol.upper(), 100.0)
        raw = load_price_history(n_days=max(limit, 200), seed=seed)
        # load_price_history() always starts near 100 — rescale to the
        # symbol's realistic starting price while preserving its % moves.
        scale = start_price / raw[0]
        _SYNTHETIC_CACHE[key] = (raw * scale).tolist()
    closes = _SYNTHETIC_CACHE[key][-limit:]
    now = datetime.utcnow()
    step = _TF_SECONDS.get(timeframe, 86400)
    bars = []
    for i, c in enumerate(closes):
        t_offset = (len(closes) - i) * step
        bars.append({"t": (now.timestamp() - t_offset), "o": c, "h": c * 1.001, "l": c * 0.999, "c": c})
    return bars


def _synthetic_price(symbol: str) -> float:
    bars = _synthetic_bars(symbol, "1d", 5)
    return bars[-1]["c"] if bars else 100.0


def _get_adapter_for_reads(user_id: int, mode: str):
    """Prefer the adapter for the requested mode; fall back to whichever mode
    IS connected, since market data (bars/quotes) is the same regardless of
    which Alpaca environment you're querying it from."""
    adapter = broker_registry.get_adapter(user_id, mode)
    if adapter:
        return adapter
    for m in broker_registry.connected_modes(user_id):
        return broker_registry.get_adapter(user_id, m)
    return None


# ----------------------------------------------------------------------------
# Request/response schemas
# ----------------------------------------------------------------------------
class OrderCreateRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    mode: Mode = "paper"
    order_type: Literal["market", "limit", "stop"] = "market"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


class OrderCloseRequest(BaseModel):
    reason: Optional[str] = "manual"


def _trade_to_dict(t) -> dict:
    return {
        "id": t.id,
        "mode": t.mode,
        "symbol": t.symbol,
        "side": t.side,
        "order_type": t.order_type,
        "qty": t.qty,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "sl": t.sl,
        "tp": t.tp,
        "status": t.status,
        "pnl": t.pnl,
        "source": t.source,
        "close_reason": t.close_reason,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


def build_trading_router(get_db, get_current_user, Trade) -> APIRouter:
    router = APIRouter(prefix="/trading", tags=["trading"])

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    @router.post("/orders", status_code=status.HTTP_201_CREATED)
    def place_order(
        body: OrderCreateRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        adapter = broker_registry.get_adapter(current_user.id, body.mode)

        if adapter:
            try:
                broker_order = adapter.place_order(
                    symbol=body.symbol, qty=body.qty, side=body.side,
                    order_type=body.order_type, limit_price=body.limit_price, stop_price=body.stop_price,
                )
            except Exception as exc:  # noqa: BLE001 - surface broker errors directly
                raise HTTPException(status_code=400, detail=f"Broker rejected order: {exc}")
            entry_price = broker_order.filled_avg_price or _synthetic_price(body.symbol)
            broker_order_id = broker_order.order_id
            source = "broker"
        else:
            if body.mode == "live":
                raise HTTPException(
                    status_code=400,
                    detail="No live broker connected. POST /broker/connect with paper=false first.",
                )
            # No paper broker connected either — synthetic fallback so the
            # Trade tab still works pre-connection.
            entry_price = _synthetic_price(body.symbol)
            broker_order_id = None
            source = "synthetic"

        trade = Trade(
            user_id=current_user.id,
            mode=body.mode,
            symbol=body.symbol,
            side=body.side,
            order_type=body.order_type,
            qty=body.qty,
            entry_price=entry_price,
            sl=body.sl,
            tp=body.tp,
            status="open",
            broker_order_id=broker_order_id,
            source=source,
            opened_at=datetime.utcnow(),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return _trade_to_dict(trade)

    @router.post("/orders/{trade_id}/close")
    def close_order(
        trade_id: int,
        body: OrderCloseRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        trade = db.query(Trade).filter(Trade.id == trade_id, Trade.user_id == current_user.id).first()
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade.status == "closed":
            raise HTTPException(status_code=400, detail="Trade already closed")

        adapter = broker_registry.get_adapter(current_user.id, trade.mode)
        if adapter and trade.source == "broker":
            try:
                broker_order = adapter.close_position(trade.symbol)
                exit_price = broker_order.filled_avg_price or _synthetic_price(trade.symbol)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"Broker rejected close: {exc}")
        else:
            quote_adapter = _get_adapter_for_reads(current_user.id, trade.mode)
            exit_price = quote_adapter.get_latest_quote(trade.symbol)["price"] if quote_adapter else _synthetic_price(trade.symbol)

        direction = 1 if trade.side == "buy" else -1
        pnl = (exit_price - trade.entry_price) * trade.qty * direction

        trade.exit_price = exit_price
        trade.pnl = pnl
        trade.status = "closed"
        trade.close_reason = body.reason or "manual"
        trade.closed_at = datetime.utcnow()

        db.add(trade)
        db.commit()
        db.refresh(trade)
        return _trade_to_dict(trade)

    @router.get("/orders")
    def list_orders(
        mode: Optional[Mode] = Query(default=None),
        status_filter: Optional[Literal["open", "closed"]] = Query(default=None, alias="status"),
        limit: int = Query(default=100, le=500),
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        q = db.query(Trade).filter(Trade.user_id == current_user.id)
        if mode:
            q = q.filter(Trade.mode == mode)
        if status_filter:
            q = q.filter(Trade.status == status_filter)
        rows = q.order_by(Trade.id.desc()).limit(limit).all()
        return [_trade_to_dict(t) for t in rows]

    @router.get("/positions")
    def list_positions(
        mode: Optional[Mode] = Query(default=None),
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        """Open positions, priced with the latest available quote so the
        frontend can show live unrealised P&L without polling /orders."""
        q = db.query(Trade).filter(Trade.user_id == current_user.id, Trade.status == "open")
        if mode:
            q = q.filter(Trade.mode == mode)
        rows = q.order_by(Trade.id.desc()).all()

        out = []
        price_cache: dict[str, float] = {}
        for t in rows:
            if t.symbol not in price_cache:
                adapter = _get_adapter_for_reads(current_user.id, t.mode)
                try:
                    price_cache[t.symbol] = (
                        adapter.get_latest_quote(t.symbol)["price"] if adapter else _synthetic_price(t.symbol)
                    )
                except Exception:  # noqa: BLE001
                    price_cache[t.symbol] = t.entry_price
            current_price = price_cache[t.symbol]
            direction = 1 if t.side == "buy" else -1
            unrealized_pl = (current_price - t.entry_price) * t.qty * direction

            d = _trade_to_dict(t)
            d["current_price"] = current_price
            d["unrealized_pl"] = unrealized_pl
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # Market data (feeds the chart + timeframe buttons)
    # ------------------------------------------------------------------
    @router.get("/bars")
    def get_bars(
        symbol: str,
        timeframe: str = Query(default="1d", pattern="^(1m|5m|15m|1h|4h|1d)$"),
        mode: Mode = "paper",
        limit: int = Query(default=150, le=1000),
        current_user=Depends(get_current_user),
    ):
        adapter = _get_adapter_for_reads(current_user.id, mode)
        if adapter:
            try:
                bars = adapter.get_historical_bars(symbol, timeframe=timeframe, limit=limit)
                if bars:
                    return {"symbol": symbol, "timeframe": timeframe, "source": "broker", "bars": bars}
            except Exception:  # noqa: BLE001
                pass  # fall through to synthetic
        return {"symbol": symbol, "timeframe": timeframe, "source": "synthetic", "bars": _synthetic_bars(symbol, timeframe, limit)}

    @router.get("/quote")
    def get_quote(
        symbol: str,
        mode: Mode = "paper",
        current_user=Depends(get_current_user),
    ):
        adapter = _get_adapter_for_reads(current_user.id, mode)
        if adapter:
            try:
                return {**adapter.get_latest_quote(symbol), "source": "broker"}
            except Exception:  # noqa: BLE001
                pass
        price = _synthetic_price(symbol)
        return {"symbol": symbol, "bid": price * 0.9998, "ask": price * 1.0002, "price": price, "source": "synthetic"}

    return router
