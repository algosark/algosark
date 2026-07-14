# Algosark SPSG Engine — MVP Integration Guide

This package implements the two components your business plan lists as
"under development": the **SPSG AI engine** and **brokerage API integration**.
Everything here is real, tested code — not a mockup — but every model is
currently trained on synthetic/rule-distilled data (clearly documented below)
since there's no real subscriber outcome data yet.

## What's in here

```
spsg/
  features.py            Survey -> investor profile (risk scores + ML embedding)
  regime.py               Market regime classifier (trending/mean-reverting/volatile/crisis)
  ensemble.py              XGBoost + LightGBM + neural net hybrid ensemble -> strategy params
  backtester.py            Walk-forward backtest + drift detection
  strategy_generator.py    Orchestrates the full pipeline -> "My Strategy" payload
  broker/
    base.py                Abstract BrokerAdapter interface
    alpaca_adapter.py       Alpaca paper/live REST implementation

models_strategy.py         SQLAlchemy `Strategy` table (factory fn, binds to your Base)
api_strategy.py             FastAPI routers: /strategy/*, /broker/*
scripts/train_models.py     Pre-trains + caches the ensemble (run once at build time)
test_integration.py          End-to-end proof the wiring works (register->generate->drift->broker)
requirements-spsg.txt        New pip dependencies
```

## 1. Install dependencies

```bash
pip install -r requirements-spsg.txt
```

## 2. Pre-train the ensemble (do this once, and again whenever you change
   `spsg/ensemble.py`'s rule-distillation logic)

```bash
python scripts/train_models.py
```

This writes to `models_cache/ensemble/` — commit that directory or bake it
into your Docker image so app startup doesn't pay a ~25s training cost.
Without it, `SPSGEngine()` auto-trains once on first import (fine locally,
not ideal for serverless cold starts).

## 3. Wire into your existing `main.py`

Add near the bottom, right after your existing `Base.metadata.create_all(bind=engine)`:

```python
from models_strategy import define_strategy_model
from api_strategy import build_strategy_router, build_broker_router

Strategy = define_strategy_model(Base)
Base.metadata.create_all(bind=engine)   # re-run: creates the new `strategies` table

app.include_router(build_strategy_router(get_db, get_current_user, SurveyResponse, Strategy))
app.include_router(build_broker_router(get_db, get_current_user))
```

That's it — no changes needed to your existing User/SurveyResponse models or
auth flow. The router factories take your existing `get_db` and
`get_current_user` dependencies directly, so there's no circular import and
no duplicate DB setup.

## 4. New endpoints you get

| Method | Path | Description |
|---|---|---|
| POST | `/strategy/generate` | Runs the full SPSG pipeline on the caller's latest survey response, persists it, returns it |
| GET | `/strategy/me` | Returns the caller's currently-active strategy |
| GET | `/strategy/history` | Lists all strategies ever generated for the caller |
| POST | `/strategy/{id}/check-drift` | Re-checks drift monitoring for a stored strategy |
| POST | `/broker/connect` | Connects an Alpaca account (paper or live) |
| GET | `/broker/account` | Returns account cash/portfolio value |
| GET | `/broker/positions` | Returns open broker positions |
| POST | `/broker/order` | Places an order (paper mode only until compliance sign-off) |
| DELETE | `/broker/positions/{symbol}` | Closes a position |

The `/strategy/generate` response is shaped to drop directly into your
`view-strategy` dashboard page — `strategy_name`, `risk_profile_score`,
`target_return_range`, and `parameters.{max_drawdown_pct, risk_per_trade_pct,
max_positions, timeframe, direction, leverage, markets, sizing}` map 1:1 onto
the fields already rendered there. `backtested_performance` maps onto the
"Backtested Performance" table.

## 5. Frontend wiring (dashboard.html)

Replace the hardcoded values in `#view-strategy` with a fetch on `showView('strategy', ...)`:

```javascript
async function loadStrategy() {
  const token = localStorage.getItem('access_token');
  let res = await fetch('/strategy/me', { headers: { Authorization: `Bearer ${token}` } });
  if (res.status === 404) {
    res = await fetch('/strategy/generate', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
  }
  const strategy = await res.json();
  // populate the existing DOM elements in #view-strategy with strategy.strategy_name,
  // strategy.risk_profile_score, strategy.parameters.*, strategy.backtested_performance.*
}
```

## 6. What's real vs. what's a documented placeholder

**Real, tested, production-shaped code:**
- Feature engineering (survey -> risk/experience/automation-readiness scores + ML embedding)
- Regime classifier (RandomForest, trained on stochastic-process-labelled synthetic paths — a
  standard bootstrapping technique; swap in real labelled market data as it accumulates)
- Hybrid ensemble (XGBoost + LightGBM + MLPRegressor, regime-aware blended, trained on
  rule-distilled synthetic labels reflecting your business plan's risk-tiering rules)
- Walk-forward backtester with drift detection
- Alpaca broker adapter (plain REST, no SDK dependency)

**Documented placeholders you must swap before going live:**
- `backtester.load_price_history()` generates synthetic GBM price data because this sandbox's
  network egress can't reach market-data providers. In your real deployment, feed it real
  historical bars — the natural source is `BrokerAdapter.get_historical_bars()`, already
  implemented for Alpaca.
- `ensemble.generate_synthetic_training_set()` distills your documented risk-tiering rules
  into labelled training data. Once you have real subscriber outcomes (with consent),
  retrain on that instead — the architecture doesn't change, only the training set does.
- `broker/api_strategy.py`'s in-memory `_adapters` dict is the simplest possible thing that
  proves the flow works end-to-end. Before production, persist broker credentials encrypted
  at rest (KMS-backed secrets column), not in process memory.

## 7. Compliance guard rails already in place

- `/broker/order` refuses to place orders unless `paper=True` was used at `/broker/connect`,
  returning a 403 with a message about pending FCA Appointed-Representative sign-off.
- Every generated strategy includes the same "past performance ≠ future results" disclaimer
  text used on the Strategy dashboard page.
- Drift detection (`detect_drift`) is designed to gate any *automatic* strategy regeneration
  behind explicit user consent — call `/strategy/{id}/check-drift` on a schedule and only
  call `/strategy/generate` again after the user has confirmed they want an update.
