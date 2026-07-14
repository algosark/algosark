"""
spsg/ensemble.py
================
Hybrid ensemble strategy-generation model.

Given an investor profile embedding (spsg/features.py) and the detected market
regime (spsg/regime.py), predicts a set of continuous strategy parameters:

    max_drawdown_target   (%)
    risk_per_trade         (%)
    target_annual_return   (%, midpoint of the range shown to the user)
    leverage                (x)
    max_positions           (int, rounded)
    holding_period_days     (avg bars held -> maps to Scalping/Intraday/Swing/Position)

Three base learners are trained per target: XGBoost, LightGBM, and a small
MLPRegressor ("neural network component" from the business plan). Their
predictions are combined by a regime-aware meta-learner that blends them with
weights that shift depending on the detected regime — e.g. in a 'crisis'
regime the meta-learner leans harder on the more conservative-leaning base
learner and shrinks leverage/position-size predictions.

Bootstrapping the training labels
----------------------------------
There is no historical "user -> ideal strategy" outcome data yet (the platform
hasn't launched). So, exactly as with the regime classifier, we bootstrap by
distilling the documented risk-tiering rules from the Algosark business plan
(SWOT / Strategy page: risk profile -> drawdown/leverage/position-sizing
bands) into a synthetic labelled dataset, with realistic noise added so the
ensemble learns smooth, interpolated relationships rather than memorising a
lookup table. As real subscriber outcomes (opt-in performance + satisfaction
data) accumulate post-launch, `train_and_save` should be re-run against real
labels — the architecture doesn't change, only the training set does.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import warnings
import numpy as np
import joblib

warnings.filterwarnings("ignore", message="X does not have valid feature names")

from xgboost import XGBRegressor
from lightgbm import LGBMRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

from .features import EMBEDDING_LENGTH
from .regime import REGIMES

TARGETS = [
    "max_drawdown_target",
    "risk_per_trade",
    "target_annual_return",
    "leverage",
    "max_positions",
    "holding_period_days",
]

# Regime -> meta-learner blend weights over the 3 base learners (xgb, lgbm, nn).
# Crisis regimes lean on the smoothest/most-regularised learner (nn) and shrink
# the final leverage/position outputs via `_regime_risk_multiplier`.
REGIME_BLEND_WEIGHTS = {
    "trending":       {"xgb": 0.45, "lgbm": 0.40, "nn": 0.15},
    "mean_reverting":  {"xgb": 0.35, "lgbm": 0.35, "nn": 0.30},
    "volatile":        {"xgb": 0.30, "lgbm": 0.30, "nn": 0.40},
    "crisis":          {"xgb": 0.20, "lgbm": 0.20, "nn": 0.60},
}

# Multiplicative risk shrinkage applied post-hoc based on regime (independent
# of the blend weights above) — this is the explicit "dynamically adjusts
# model weightings to optimise strategy performance" behaviour.
REGIME_RISK_MULTIPLIER = {
    "trending": 1.00,
    "mean_reverting": 0.90,
    "volatile": 0.75,
    "crisis": 0.50,
}


# ----------------------------------------------------------------------------
# Synthetic label generation (rule distillation with noise)
# ----------------------------------------------------------------------------
def _rule_based_targets(embedding: np.ndarray, rng: np.random.Generator) -> dict:
    """
    embedding layout (see features.py):
      [.. , risk_capacity, risk_tolerance, experience_score, automation_readiness,
       time_horizon_score, return_ambition] are the LAST 5 entries (indices -5..-1),
       preceded by overall composite risk_capacity at index -6.
    We recompute a simple composite risk score directly from the embedding so this
    module has no import-time dependency on features.py's dataclass.
    """
    risk_capacity = embedding[-6]
    risk_tolerance = embedding[-5]
    experience = embedding[-4]
    automation = embedding[-3]
    horizon = embedding[-2]
    return_ambition = embedding[-1]

    composite_risk = np.clip(
        0.35 * risk_tolerance + 0.30 * risk_capacity + 0.20 * experience + 0.15 * return_ambition, 0, 1
    )

    # Banded rules straight from the business-plan "Strategy Parameters" table,
    # interpolated continuously by composite_risk instead of hard tiers, then
    # perturbed with small noise so the model learns a smooth function.
    max_drawdown_target = 10 + 25 * composite_risk               # 10% (conservative) .. 35% (aggressive)
    risk_per_trade = 0.5 + 3.5 * composite_risk                    # 0.5% .. 4%
    target_annual_return = 8 + 52 * composite_risk                 # 8% .. 60%
    leverage = 1 + 4 * composite_risk * (0.3 + 0.7 * automation)   # capped by automation/API comfort readiness
    max_positions = 2 + 8 * composite_risk                         # 2 .. 10 concurrent positions
    holding_period_days = 30 - 27 * composite_risk                 # 30 (position trader) .. ~3 (short swing)
    holding_period_days = np.clip(holding_period_days * (0.6 + 0.8 * (1 - horizon)), 1, 30)

    noise = lambda scale: rng.normal(0, scale)
    return {
        "max_drawdown_target": float(np.clip(max_drawdown_target + noise(1.5), 5, 45)),
        "risk_per_trade": float(np.clip(risk_per_trade + noise(0.2), 0.25, 5)),
        "target_annual_return": float(np.clip(target_annual_return + noise(3), 5, 80)),
        "leverage": float(np.clip(leverage + noise(0.3), 1, 6)),
        "max_positions": float(np.clip(max_positions + noise(0.6), 1, 12)),
        "holding_period_days": float(np.clip(holding_period_days + noise(1.0), 1, 30)),
    }


def generate_synthetic_training_set(n_samples: int = 4000, seed: int = 7):
    """Samples random-but-plausible investor embeddings and their rule-distilled
    strategy-parameter labels. Returns (X, y_dict)."""
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, size=(n_samples, EMBEDDING_LENGTH))
    y = {t: np.zeros(n_samples) for t in TARGETS}
    for i in range(n_samples):
        targets = _rule_based_targets(X[i], rng)
        for t in TARGETS:
            y[t][i] = targets[t]
    return X, y


# ----------------------------------------------------------------------------
# Ensemble container
# ----------------------------------------------------------------------------
@dataclass
class SPSGEnsemble:
    scaler: StandardScaler
    models: dict  # target -> {"xgb": model, "lgbm": model, "nn": model}

    @classmethod
    def train(cls, n_samples: int = 4000) -> "SPSGEnsemble":
        X, y = generate_synthetic_training_set(n_samples)
        scaler = StandardScaler().fit(X)
        X_scaled = scaler.transform(X)

        models = {}
        for target in TARGETS:
            yt = y[target]
            xgb = XGBRegressor(
                n_estimators=250, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0,
            ).fit(X_scaled, yt)

            lgbm = LGBMRegressor(
                n_estimators=250, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=-1,
            ).fit(X_scaled, yt)

            nn = MLPRegressor(
                hidden_layer_sizes=(32, 16), activation="relu", alpha=1e-3,
                max_iter=2000, random_state=42,
            ).fit(X_scaled, yt)

            models[target] = {"xgb": xgb, "lgbm": lgbm, "nn": nn}

        return cls(scaler=scaler, models=models)

    def predict(self, embedding: np.ndarray, regime: str = "trending") -> dict:
        """Regime-aware weighted blend of the three base learners' predictions."""
        weights = REGIME_BLEND_WEIGHTS.get(regime, REGIME_BLEND_WEIGHTS["trending"])
        risk_mult = REGIME_RISK_MULTIPLIER.get(regime, 1.0)

        x_scaled = self.scaler.transform(embedding.reshape(1, -1))
        out = {}
        for target, base_models in self.models.items():
            preds = {name: float(m.predict(x_scaled)[0]) for name, m in base_models.items()}
            blended = sum(preds[name] * w for name, w in weights.items())
            out[target] = {"blended": blended, "base_predictions": preds}

        # Apply regime risk shrinkage to the risk-bearing outputs only.
        for risky_target in ("risk_per_trade", "leverage", "max_positions", "max_drawdown_target"):
            out[risky_target]["blended"] *= risk_mult

        return out

    # -- persistence ----------------------------------------------------
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.scaler, path / "scaler.joblib")
        for target, base_models in self.models.items():
            joblib.dump(base_models, path / f"models_{target}.joblib")
        (path / "meta.json").write_text(json.dumps({"targets": TARGETS, "regimes": REGIMES}))

    @classmethod
    def load(cls, path: str | Path) -> "SPSGEnsemble":
        path = Path(path)
        scaler = joblib.load(path / "scaler.joblib")
        models = {}
        for target in TARGETS:
            models[target] = joblib.load(path / f"models_{target}.joblib")
        return cls(scaler=scaler, models=models)
