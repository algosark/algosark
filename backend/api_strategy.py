"""
api_strategy.py
================
FastAPI routes for strategy generation and (paper) broker execution. Built as
router *factories* that accept your existing app's `get_db`, `get_current_user`,
`SurveyResponse`, and `Strategy` (see models_strategy.py) as parameters — this
avoids any circular import between this file and main.py.

Wire-up in main.py (add near the bottom, after `Base.metadata.create_all`):

    from models_strategy import define_strategy_model
    from api_strategy import build_strategy_router, build_broker_router

    Strategy = define_strategy_model(Base)
    Base.metadata.create_all(bind=engine)  # re-run so the strategies table gets created

    app.include_router(build_strategy_router(get_db, get_current_user, SurveyResponse, Strategy))
    app.include_router(build_broker_router(get_db, get_current_user))
"""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

import broker_registry
from spsg import SPSGEngine
from spsg.broker import AlpacaAdapter

# ----------------------------------------------------------------------------
# The SPSG engine trains/loads its models once per process (see SPSGEngine.__init__),
# so we keep a single module-level instance rather than re-instantiating per request.
# ----------------------------------------------------------------------------
_engine_instance: SPSGEngine | None = None


def get_spsg_engine() -> SPSGEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SPSGEngine()
    return _engine_instance


def _survey_row_to_dict(survey_row) -> dict:
    return {c.name: getattr(survey_row, c.name) for c in survey_row.__table__.columns}


def build_strategy_router(get_db, get_current_user, SurveyResponse, Strategy) -> APIRouter:
    router = APIRouter(prefix="/strategy", tags=["strategy"])

    @router.post("/generate", status_code=status.HTTP_201_CREATED)
    def generate_strategy(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        survey = (
            db.query(SurveyResponse)
            .filter(SurveyResponse.user_id == current_user.id)
            .order_by(SurveyResponse.id.desc())
            .first()
        )
        if not survey:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Complete the eligibility & risk-assessment survey before generating a strategy.",
            )

        engine = get_spsg_engine()
        survey_dict = _survey_row_to_dict(survey)
        result = engine.generate_strategy(survey_dict)

        # Supersede any previous active strategy for this user.
        db.query(Strategy).filter(Strategy.user_id == current_user.id, Strategy.is_active == 1).update(
            {"is_active": 0}
        )

        db_strategy = Strategy(
            user_id=current_user.id,
            strategy_name=result["strategy_name"],
            risk_profile_score=result["risk_profile_score"],
            detected_regime=result["regime_detection"]["detected_regime"],
            payload=result,
            is_active=1,
        )
        db.add(db_strategy)
        db.commit()
        db.refresh(db_strategy)

        return result

    @router.get("/me")
    def get_my_strategy(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        strategy = (
            db.query(Strategy)
            .filter(Strategy.user_id == current_user.id, Strategy.is_active == 1)
            .order_by(Strategy.id.desc())
            .first()
        )
        if not strategy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No strategy generated yet. POST /strategy/generate first.",
            )
        return strategy.payload

    @router.get("/history")
    def get_strategy_history(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        rows = (
            db.query(Strategy)
            .filter(Strategy.user_id == current_user.id)
            .order_by(Strategy.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "strategy_name": r.strategy_name,
                "risk_profile_score": r.risk_profile_score,
                "detected_regime": r.detected_regime,
                "is_active": bool(r.is_active),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    @router.post("/{strategy_id}/check-drift")
    def check_drift(
        strategy_id: int,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        """Re-checks the stored strategy's drift_monitoring block. In production
        this should be run on a schedule (e.g. nightly) rather than on demand,
        with the user prompted for consent before any strategy is regenerated —
        see the business plan's 'consent-based strategy updates' requirement."""
        strategy = (
            db.query(Strategy)
            .filter(Strategy.id == strategy_id, Strategy.user_id == current_user.id)
            .first()
        )
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return strategy.payload.get("drift_monitoring", {"needs_review": False, "reason": "not_available"})

    return router


# ----------------------------------------------------------------------------
# Broker connectivity. Uses broker_registry.py so a user can have a paper AND
# a live Alpaca connection active simultaneously (Alpaca uses separate key
# pairs per environment) — the dashboard's paper/live toggle just switches
# which one subsequent requests use, no reconnect needed.
#
# Live trading note: this previously hard-blocked live orders behind a 403
# pending compliance sign-off. That block has been lifted at the user's
# request — live orders now go through as long as a live-mode broker
# connection exists. As a safety rail (not a compliance substitute), placing
# a live order still requires the caller to pass `confirm_live: true`
# explicitly; the dashboard shows a confirmation dialog for this. Actual
# order placement/closing for the Trade tab should go through
# api_trading.py's /trading/orders endpoints instead of /broker/order below,
# since those also persist to the trades table — /broker/order is kept for
# direct broker-only use (e.g. scripts/run_live_strategy.py).
# ----------------------------------------------------------------------------
class BrokerConnectRequest(BaseModel):
    provider: str = "alpaca"
    api_key: str
    api_secret: str
    paper: bool = True


class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str  # "buy" | "sell"
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    confirm_live: bool = False


def build_broker_router(get_db, get_current_user) -> APIRouter:
    router = APIRouter(prefix="/broker", tags=["broker"])

    @router.post("/connect")
    def connect_broker(
        body: BrokerConnectRequest,
        current_user=Depends(get_current_user),
    ):
        if body.provider != "alpaca":
            raise HTTPException(status_code=400, detail=f"Provider '{body.provider}' not yet supported")
        adapter = AlpacaAdapter()
        adapter.connect(body.api_key, body.api_secret, paper=body.paper)
        try:
            account = adapter.get_account()
        except Exception as exc:  # noqa: BLE001 - surface broker errors directly
            raise HTTPException(status_code=400, detail=f"Could not connect to Alpaca: {exc}")
        broker_registry.set_adapter(current_user.id, body.paper, adapter)
        return {
            "connected": True, "provider": "alpaca", "paper": body.paper,
            "mode": broker_registry.mode_key(body.paper), "account": account.__dict__,
        }

    @router.get("/status")
    def broker_status(current_user=Depends(get_current_user)):
        """Which modes (paper/live) currently have a connected broker."""
        return {"connected_modes": broker_registry.connected_modes(current_user.id)}

    @router.get("/account")
    def get_account(mode: str = "paper", current_user=Depends(get_current_user)):
        adapter = broker_registry.get_adapter(current_user.id, mode)
        if not adapter:
            raise HTTPException(status_code=400, detail=f"No {mode} broker connected. POST /broker/connect first.")
        return adapter.get_account().__dict__

    @router.get("/positions")
    def get_positions(mode: str = "paper", current_user=Depends(get_current_user)):
        adapter = broker_registry.get_adapter(current_user.id, mode)
        if not adapter:
            raise HTTPException(status_code=400, detail=f"No {mode} broker connected. POST /broker/connect first.")
        return [p.__dict__ for p in adapter.get_positions()]

    @router.post("/order")
    def place_order(body: OrderRequest, mode: str = "paper", current_user=Depends(get_current_user)):
        adapter = broker_registry.get_adapter(current_user.id, mode)
        if not adapter:
            raise HTTPException(status_code=400, detail=f"No {mode} broker connected. POST /broker/connect first.")
        if mode == "live" and not body.confirm_live:
            raise HTTPException(
                status_code=400,
                detail="Live order requires confirm_live=true — this is a real-money trade.",
            )
        order = adapter.place_order(
            symbol=body.symbol, qty=body.qty, side=body.side,
            order_type=body.order_type, limit_price=body.limit_price, stop_price=body.stop_price,
        )
        return order.__dict__

    @router.delete("/positions/{symbol}")
    def close_position(symbol: str, mode: str = "paper", current_user=Depends(get_current_user)):
        adapter = broker_registry.get_adapter(current_user.id, mode)
        if not adapter:
            raise HTTPException(status_code=400, detail=f"No {mode} broker connected. POST /broker/connect first.")
        order = adapter.close_position(symbol)
        return order.__dict__

    @router.post("/disconnect")
    def disconnect_broker(mode: str = "paper", current_user=Depends(get_current_user)):
        broker_registry.clear_adapter(current_user.id, mode)
        return {"disconnected": True, "mode": mode}

    return router
