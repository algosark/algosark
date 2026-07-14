# Algosark — Full System

This is the complete, wired-together backend: auth, survey, the SPSG AI
strategy engine, broker connectivity (paper + live), and persisted trade
history — everything the dashboard's Trade/Strategy/Profile/Settings tabs
call. Verified end-to-end in `tests/test_full_system.py` (register → login →
survey → generate strategy → place/close a trade → confirm it persists).

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Pre-train the SPSG ensemble (optional but recommended)

```bash
python scripts/train_models.py
```

Without this, the first request to `/strategy/generate` after a fresh start
trains the ensemble on the fly (~20-30s). With it, startup is instant. Either
way, models are cached to `models_cache/`.

## 3. Put your frontend files in `static/`

```
static/
  index.html
  login.html
  survey.html      (already here)
  dashboard.html    (already here — the one wired to this backend)
```

`main.py` serves each of these at its own clean URL (`/dashboard.html`,
`/survey.html`, etc.) via explicit routes, plus mounts the rest of `static/`
at `/static` for any additional assets (CSS, images, JS you split out later).

## 4. Run it

```bash
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/index.html` (or `/login.html` to start).

## 5. What's wired to what

| Dashboard feature | Endpoint(s) | File |
|---|---|---|
| Register / Login | `POST /api/register`, `POST /api/login` | `main.py` |
| Profile view/edit | `GET /users/me`, `PATCH /users/me` | `main.py`, `api_profile.py` |
| Eligibility survey | `POST /api/survey` | `main.py`, `schema.py` |
| My Strategy tab | `POST /strategy/generate`, `GET /strategy/me`, `GET /strategy/history` | `api_strategy.py`, `spsg/` |
| Settings → Broker Connection | `POST /broker/connect`, `GET /broker/status`, `GET /broker/account` | `api_strategy.py`, `broker_registry.py` |
| Trade tab — chart + timeframes | `GET /trading/bars`, `GET /trading/quote` | `api_trading.py` |
| Trade tab — place/close orders | `POST /trading/orders`, `POST /trading/orders/{id}/close` | `api_trading.py` |
| Positions / History tabs | `GET /trading/positions`, `GET /trading/orders` | `api_trading.py` |

## 6. Database

One SQLite file, `users.db`, created automatically on first run. Tables:
`users`, `survey_responses` (from `main.py`), `strategies` (from
`models_strategy.py`), `trades` (from `models_trade.py`). All four share the
same `Base`/`engine`, so it's one file, one set of migrations to think about.

## 7. Things that are real vs. explicitly placeholder

**Real:** auth (bcrypt + JWT), survey persistence, the SPSG ensemble
(XGBoost + LightGBM + neural net, regime-aware), walk-forward backtesting,
the Alpaca REST adapter, trade persistence, timeframe-aware chart data.

**Placeholder, clearly flagged in code/API responses:**
- SPSG training data is rule-distilled synthetic data (no real subscriber
  outcomes exist yet) — see `README_SPSG_INTEGRATION.md`.
- Trade fills fall back to synthetic prices when no broker is connected for
  the requested mode — every trade record has `"source": "broker"` or
  `"source": "synthetic"` so this is never silently hidden from the user.
- `broker_registry.py` stores connections in process memory — fine for one
  worker/one process, not production-ready for multi-worker deployments
  (documented in that file's docstring with the exact fix needed).
- Backtests run on synthetic price history, not real market data, because
  this dev sandbox can't reach market-data providers — swap
  `spsg/backtester.py`'s `load_price_history()` for real historical bars
  (available via `BrokerAdapter.get_historical_bars()`) before trusting the
  numbers for anything real.

## 8. Known cosmetic issue worth fixing before demoing

The synthetic price fallback in `api_trading.py` generates prices via a
generic GBM process starting at 100, regardless of what the symbol actually
is — so a synthetic-fallback GBPUSD trade might show an entry price like
"133.85" instead of something forex-realistic like "1.27". It's harmless
(P&L math is still internally consistent) but looks odd. Fix: seed
`_synthetic_price()` per-symbol starting price (e.g. from `SYMBOL_DECIMALS`-
adjacent starting-price table) rather than always starting at 100.
