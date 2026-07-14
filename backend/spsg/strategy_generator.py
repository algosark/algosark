"""
spsg/strategy_generator.py
===========================
Top-level orchestrator for the Survey-Driven Personalised Strategy Generator.

generate_strategy(survey_dict, market_prices) runs the full pipeline described
in the business plan's Operational Model diagram:

    survey -> feature engineering -> regime detection -> hybrid ensemble
    -> personalised strategy -> walk-forward backtest -> Strategy payload

and returns a dict shaped to match exactly what the dashboard's "My Algosark
Strategy" page (view-strategy in dashboard.html) expects to render.
"""

from __future__ import annotations
from pathlib import Path
import numpy as np

from .features import build_investor_profile
from .regime import RegimeClassifier
from .ensemble import SPSGEnsemble
from .backtester import load_price_history, walk_forward_backtest, detect_drift

MODEL_DIR = Path(__file__).resolve().parent.parent / "models_cache"

MARKET_LABELS = {
    "forex": "Forex",
    "equities": "US & UK Equities",
    "crypto": "Crypto",
    "indices": "Indices",
    "commodities": "Commodities",
}

DIRECTION_BY_RISK = lambda risk: "Long & Short" if risk >= 40 else "Long Only"

STRATEGY_NAME_BANDS = [
    (0, 30, "Capital Preservation Trend"),
    (30, 55, "Balanced Momentum"),
    (55, 75, "ML-Hybrid Momentum"),
    (75, 101, "Aggressive Alpha-Seeker"),
]


def _strategy_name(risk_score: int) -> str:
    for lo, hi, name in STRATEGY_NAME_BANDS:
        if lo <= risk_score < hi:
            return name
    return "Balanced Momentum"


def _timeframe_label(holding_period_days: float) -> str:
    if holding_period_days <= 1.5:
        return "Intraday"
    if holding_period_days <= 5:
        return "Short Swing (days)"
    if holding_period_days <= 15:
        return "Swing (days)"
    return "Position (weeks)"


class SPSGEngine:
    """Loads (or lazily trains) the regime classifier + ensemble once, then
    serves generate_strategy() calls cheaply. Instantiate this once at app
    startup (see api_strategy.py) rather than per-request."""

    def __init__(self, model_dir: Path | str = MODEL_DIR, auto_train_if_missing: bool = True):
        self.model_dir = Path(model_dir)
        self.regime_clf: RegimeClassifier | None = None
        self.ensemble: SPSGEnsemble | None = None
        self._load_or_train(auto_train_if_missing)

    def _load_or_train(self, auto_train: bool):
        ensemble_path = self.model_dir / "ensemble"
        try:
            self.ensemble = SPSGEnsemble.load(ensemble_path)
        except FileNotFoundError:
            if not auto_train:
                raise
            self.ensemble = SPSGEnsemble.train(n_samples=4000)
            self.ensemble.save(ensemble_path)

        # Regime classifier is cheap to train (<1s) and has no meaningful
        # benefit from disk caching, so we just retrain it at process start.
        self.regime_clf = RegimeClassifier.train(n_samples_per_regime=250)

    def generate_strategy(self, survey: dict, market_prices: dict[str, np.ndarray] | None = None) -> dict:
        """
        survey: dict of SurveyResponse columns for one user.
        market_prices: optional {market_label: price_array} for regime
            detection + backtesting per selected target market. Falls back to
            synthetic data (see backtester.load_price_history) if omitted —
            fine for demoing the pipeline before real market-data/broker
            integration is wired up.
        """
        profile = build_investor_profile(survey)

        target_markets = _safe_list(survey.get("target_markets")) or ["forex"]
        primary_market = target_markets[0].lower()
        prices = None
        if market_prices and primary_market in market_prices:
            prices = np.asarray(market_prices[primary_market])
        if prices is None or len(prices) < 60:
            prices = load_price_history(n_days=1500)

        regime_info = self.regime_clf.predict(prices[-90:] if len(prices) >= 90 else prices)
        regime = regime_info["regime"]

        raw_params = self.ensemble.predict(profile.embedding, regime=regime)
        params = {k: v["blended"] for k, v in raw_params.items()}

        max_drawdown = round(params["max_drawdown_target"], 1)
        risk_per_trade = round(params["risk_per_trade"], 2)
        leverage = round(max(1.0, params["leverage"]), 1)
        max_positions = max(1, int(round(params["max_positions"])))
        holding_period_days = max(1.0, params["holding_period_days"])
        target_return_low = round(max(params["target_annual_return"] - 10, 3))
        target_return_high = round(params["target_annual_return"] + 10)

        backtest = walk_forward_backtest(
            prices,
            holding_period_days=holding_period_days,
            risk_per_trade_pct=risk_per_trade,
            leverage=leverage,
            max_drawdown_target_pct=max_drawdown,
        )
        drift = detect_drift(backtest["fold_sharpes"])

        strategy = {
            "strategy_name": _strategy_name(profile.overall_risk_score),
            "status": "Paper Active",
            "risk_profile_score": profile.overall_risk_score,
            "target_return_range": f"{target_return_low}-{target_return_high}% p.a.",
            "parameters": {
                "max_drawdown_pct": max_drawdown,
                "risk_per_trade_pct": risk_per_trade,
                "max_positions": max_positions,
                "timeframe": _timeframe_label(holding_period_days),
                "holding_period_days": round(holding_period_days, 1),
                "direction": DIRECTION_BY_RISK(profile.overall_risk_score),
                "leverage": f"Up to {leverage}:1",
                "markets": ", ".join(MARKET_LABELS.get(m.lower(), m.title()) for m in target_markets),
                "sizing": f"Fixed {risk_per_trade}% of portfolio per trade",
            },
            "regime_detection": {
                "detected_regime": regime,
                "confidence": round(regime_info["confidence"], 3),
                "probabilities": {k: round(v, 3) for k, v in regime_info["probabilities"].items()},
            },
            "backtested_performance": backtest["full_period"],
            "drift_monitoring": drift,
            "investor_profile": profile.to_dict(),
            "disclaimer": (
                "Backtested performance does not guarantee future results. Past returns are "
                "hypothetical and simulated on historical/synthetic data. Real trading involves "
                "risks not captured in backtests including slippage, execution delays, and "
                "changing market regimes."
            ),
        }
        return strategy


def _safe_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    import json
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [v.strip() for v in str(value).split(",") if v.strip()]
