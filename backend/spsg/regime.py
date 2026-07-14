"""
spsg/regime.py
==============
Market Regime Detection.

Classifies a price series into one of: 'trending', 'volatile', 'mean_reverting',
'crisis'. This feeds the "Regime-Aware Meta-Learner" in ensemble.py, which
re-weights the XGBoost / LightGBM / neural-net base learners depending on the
detected regime.

Bootstrapping approach
-----------------------
Since we don't have years of labelled regime data on day one, we bootstrap the
classifier on *synthetic* price paths generated from four well-understood
stochastic processes, each standing in for one regime:

  - trending        -> Geometric Brownian Motion with meaningful positive/negative drift
  - mean_reverting   -> Ornstein-Uhlenbeck process (pulls back to a long-run mean)
  - volatile         -> GBM with drift ~0 but high volatility
  - crisis           -> GBM with strong negative drift + volatility spike + jumps

This is a standard technique for cold-starting a regime classifier: derive
labels from the *data-generating process* rather than from noisy human labels,
train on that, then let live performance monitoring (see backtester.py's
`detect_drift`) flag when the live classifier stops matching realised outcomes
so it can be retrained on real market data as it accumulates.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from sklearn.ensemble import RandomForestClassifier

REGIMES = ["trending", "mean_reverting", "volatile", "crisis"]


# ----------------------------------------------------------------------------
# Feature extraction from a raw price series
# ----------------------------------------------------------------------------
def extract_regime_features(prices: np.ndarray) -> np.ndarray:
    """
    prices: 1D array of closing prices (most recent last), ideally >= 60 points.
    Returns a fixed-length feature vector describing the recent regime.
    """
    prices = np.asarray(prices, dtype=np.float64)
    if len(prices) < 10:
        raise ValueError("Need at least 10 price points to extract regime features")

    rets = np.diff(np.log(prices))

    # Trend strength: R^2 of a linear fit to price, sign gives direction
    x = np.arange(len(prices))
    slope, intercept = np.polyfit(x, prices, 1)
    fitted = slope * x + intercept
    ss_res = np.sum((prices - fitted) ** 2)
    ss_tot = np.sum((prices - prices.mean()) ** 2) + 1e-9
    trend_r2 = 1 - ss_res / ss_tot
    trend_direction = np.sign(slope)

    # Realised volatility (annualised, assuming daily bars)
    vol = rets.std() * np.sqrt(252)

    # Autocorrelation of returns at lag 1 (negative -> mean-reverting tendency)
    if len(rets) > 2:
        autocorr = np.corrcoef(rets[:-1], rets[1:])[0, 1]
        if np.isnan(autocorr):
            autocorr = 0.0
    else:
        autocorr = 0.0

    # Drawdown from rolling peak (crisis signal)
    running_max = np.maximum.accumulate(prices)
    drawdown = (prices - running_max) / running_max
    max_drawdown = drawdown.min()

    # Skewness of returns (crisis / jump risk signal)
    skew = ((rets - rets.mean()) ** 3).mean() / (rets.std() ** 3 + 1e-9)

    return np.array([trend_r2, trend_direction, vol, autocorr, max_drawdown, skew], dtype=np.float64)


REGIME_FEATURE_NAMES = ["trend_r2", "trend_direction", "annual_vol", "autocorr_lag1", "max_drawdown", "return_skew"]


# ----------------------------------------------------------------------------
# Synthetic path generators (one per regime) used to bootstrap the classifier
# ----------------------------------------------------------------------------
def _gen_gbm(n: int, mu: float, sigma: float, s0: float = 100.0, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    dt = 1 / 252
    shocks = rng.normal((mu - 0.5 * sigma ** 2) * dt, sigma * np.sqrt(dt), size=n)
    return s0 * np.exp(np.cumsum(shocks))


def _gen_ou(n: int, theta: float, mu: float, sigma: float, s0: float = 100.0, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    dt = 1 / 252
    path = np.empty(n)
    path[0] = s0
    for t in range(1, n):
        path[t] = path[t - 1] + theta * (mu - path[t - 1]) * dt + sigma * np.sqrt(dt) * rng.normal()
    return path


def _gen_crisis(n: int, sigma: float, s0: float = 100.0, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    dt = 1 / 252
    mu = -0.6  # strong negative annualised drift
    shocks = rng.normal((mu - 0.5 * sigma ** 2) * dt, sigma * np.sqrt(dt), size=n)
    # Sprinkle in a few negative jumps
    n_jumps = max(1, n // 20)
    jump_idx = rng.choice(n, size=n_jumps, replace=False)
    shocks[jump_idx] -= rng.uniform(0.03, 0.08, size=n_jumps)
    return s0 * np.exp(np.cumsum(shocks))


def generate_synthetic_regime_dataset(n_samples_per_regime: int = 300, path_len: int = 90, seed: int = 42):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(n_samples_per_regime):
        drift = rng.choice([-1, 1]) * rng.uniform(0.15, 0.45)
        path = _gen_gbm(path_len, mu=drift, sigma=rng.uniform(0.08, 0.18), rng=rng)
        X.append(extract_regime_features(path)); y.append("trending")

        path = _gen_ou(path_len, theta=rng.uniform(3, 8), mu=100.0, sigma=rng.uniform(0.5, 1.5), rng=rng)
        X.append(extract_regime_features(path)); y.append("mean_reverting")

        path = _gen_gbm(path_len, mu=rng.uniform(-0.05, 0.05), sigma=rng.uniform(0.35, 0.65), rng=rng)
        X.append(extract_regime_features(path)); y.append("volatile")

        path = _gen_crisis(path_len, sigma=rng.uniform(0.4, 0.7), rng=rng)
        X.append(extract_regime_features(path)); y.append("crisis")

    return np.array(X), np.array(y)


@dataclass
class RegimeClassifier:
    model: RandomForestClassifier

    @classmethod
    def train(cls, n_samples_per_regime: int = 300) -> "RegimeClassifier":
        X, y = generate_synthetic_regime_dataset(n_samples_per_regime)
        clf = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5, random_state=42, class_weight="balanced"
        )
        clf.fit(X, y)
        return cls(model=clf)

    def predict(self, prices: np.ndarray) -> dict:
        feats = extract_regime_features(prices).reshape(1, -1)
        proba = self.model.predict_proba(feats)[0]
        classes = self.model.classes_
        regime = classes[np.argmax(proba)]
        return {
            "regime": regime,
            "confidence": float(np.max(proba)),
            "probabilities": {c: float(p) for c, p in zip(classes, proba)},
            "features": dict(zip(REGIME_FEATURE_NAMES, extract_regime_features(prices).tolist())),
        }
