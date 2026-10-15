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

## 2. Configure auth, email, and paper trading

Copy `.env.example` to `.env` and set OAuth client values, session/JWT
secrets, and SMTP credentials. Registration email verification and password
reset require working SMTP settings. New registrations send a verification
email to the user and a signed, time-limited strategy-generation link to
`ADMIN_EMAIL` (defaults to `oluwadaretomiwa@gmail.com`). The admin link only
generates after the user has completed the risk survey.

`ALPACA_KEY` and `ALPACA_SECRET` configure the shared Alpaca paper account
used by default for every user. Set `ALPACA_PAPER=true`; the application
refuses to use the shared account if it is configured as live. Without these
credentials, the local simulated paper adapter is used instead.

GitHub OAuth must use the callback URL configured in `GITHUB_REDIRECT_URI`.
In Google Cloud Console, configure the OAuth client ID matching
`GOOGLE_CLIENT_ID` as a **Web application** and add each browser origin where
the app is served under **Authorized JavaScript origins** (for this
deployment: `https://algosark.co.uk`; also add `https://www.algosark.co.uk`
if users access the site through `www`). Enter origins only—scheme and host,
with no path or trailing slash. For local development, add
`http://localhost:8000` and/or `http://127.0.0.1:8000` if those are the
origins actually used in the browser. The Google popup code flow exchanges
the code at the backend; this is not a Google redirect URI setting.

For GitHub, register the exact callback URL from `GITHUB_REDIRECT_URI`
(`https://algosark.co.uk/auth/github/callback` for this deployment) in the
OAuth App's **Authorization callback URL**. The browser origin and provider
callback URL are different settings.

## 3. Pre-train the SPSG ensemble (optional but recommended)

```bash
python scripts/train_models.py
```

Without this, the first request to `/strategy/generate` after a fresh start
trains the ensemble on the fly (~20-30s). With it, startup is instant. Either
way, models are cached to `models_cache/`. The cached ensemble records its
XGBoost, LightGBM, and scikit-learn versions and is smoke-tested when loaded;
an incompatible cache is logged and retrained automatically. After deploying
dependency updates, install `requirements.txt` and run this training command
once as part of the deployment to avoid triggering a cold retrain at runtime.
XGBoost 1.7.6 requires NumPy below 2.

## 4. Put your frontend files in `static/`

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

## 5. Run it

```bash
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/index.html` (or `/login.html` to start).

## 6. What's wired to what

| Dashboard feature | Endpoint(s) | File |
|---|---|---|
| Register / Login / verification / reset | `POST /api/register`, `POST /api/login`, `/api/verify-email`, `/api/password/*` | `main.py`, `social_login.py` |
| Profile view/edit | `GET /users/me`, `PATCH /users/me` | `main.py`, `api_profile.py` |
| Eligibility survey | `POST /api/survey` | `main.py`, `schema.py` |
| My Strategy tab | `POST /strategy/request`, `POST /strategy/admin-generate`, `GET /strategy/me`, `GET /strategy/history` | `api_strategy.py`, `account_notifications.py`, `spsg/` |
| Settings → Broker Connection | `POST /broker/connect`, `GET /broker/status`, `GET /broker/account` | `api_strategy.py`, `broker_registry.py` |
| Trade tab — chart + timeframes | `GET /trading/bars`, `GET /trading/quote` | `api_trading.py` |
| Trade tab — supported Alpaca assets | `GET /trading/assets` | `api_trading.py`, `spsg/broker/alpaca_adapter.py` |
| Trade tab — place/close orders | `POST /trading/orders`, `POST /trading/orders/{id}/close` | `api_trading.py` |
| Positions / History tabs | `GET /trading/positions`, `GET /trading/orders` | `api_trading.py` |
| Dashboard notification bell | Recent strategy, paper-trade, and live-trade activity | `static/dashboard.html` |

Order entry searches Alpaca's active, tradable US-equity catalog and validates
the selected asset and fractional-share eligibility again before submission.
For a personal Alpaca connection, recent broker orders are merged into the
order list in the selected mode. The shared paper account's broker-wide history
is intentionally not exposed across users; each user's site-created orders
remain visible from their own records.

## 7. Database

One SQLite file, `users.db`, created automatically on first run. Tables:
`users`, `auth_tokens`, `survey_responses` (from `main.py`), `strategies` (from
`models_strategy.py`), `trades` (from `models_trade.py`). All share the
same `Base`/`engine`, so it's one file, one set of migrations to think about.

## 8. Things that are real vs. explicitly placeholder

**Real:** auth (bcrypt + JWT), survey persistence, the SPSG ensemble
(XGBoost + LightGBM + neural net, regime-aware), walk-forward backtesting,
the Alpaca REST adapter, trade persistence, timeframe-aware chart data.

**Placeholder, clearly flagged in code/API responses:**
- SPSG training data is rule-distilled synthetic data (no real subscriber
  outcomes exist yet) — see `README_SPSG_INTEGRATION.md`.
- Paper orders use the configured shared Alpaca paper account or the local
  paper simulator when no Alpaca keys are configured. Live orders require a
  user's own live Alpaca connection. The synthetic order fallback remains
  defensive; trade rows identify their source explicitly.
- `broker_registry.py` stores connections in process memory — fine for one
  worker/one process, not production-ready for multi-worker deployments
  (documented in that file's docstring with the exact fix needed).
- Alpaca is the only personal broker connection currently implemented.
  Interactive Brokers, IG, Saxo, and Binance are visible in the provider
  catalog but intentionally disabled until their provider-specific auth and
  trading adapters are implemented. New users use the shared paper account
  from `.env` or the local simulator.
- Backtests run on synthetic price history, not real market data, because
  this dev sandbox can't reach market-data providers — swap
  `spsg/backtester.py`'s `load_price_history()` for real historical bars
  (available via `BrokerAdapter.get_historical_bars()`) before trusting the
  numbers for anything real.

## 9. Known cosmetic issue worth fixing before demoing

The synthetic price fallback in `api_trading.py` generates prices via a
generic GBM process starting at 100, regardless of what the symbol actually
is — so a synthetic-fallback GBPUSD trade might show an entry price like
"133.85" instead of something forex-realistic like "1.27". It's harmless
(P&L math is still internally consistent) but looks odd. Fix: seed
`_synthetic_price()` per-symbol starting price (e.g. from `SYMBOL_DECIMALS`-
adjacent starting-price table) rather than always starting at 100.
