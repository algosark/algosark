# How to Run a Strategy — Operator Guide

This covers the full lifecycle: a user completes the survey, gets a strategy,
watches it in the (already-built) simulated paper trading dashboard, then —
once they subscribe and connect a real brokerage account — the same strategy
gets executed for real (in Alpaca's *paper* environment first, then live).

There are two distinct "paper trading" concepts in this codebase — don't
conflate them:

| | Dashboard paper trading (already built) | Broker paper trading (this guide) |
|---|---|---|
| Where it runs | Client-side JS in `dashboard.html`, simulated prices | Alpaca's real paper-trading API, real market data |
| Purpose | Let a free-tier user *see* a strategy work before subscribing | Let a paying subscriber's strategy trade with fake money against real markets, as a final rehearsal before going live |
| Needs the runner in this guide? | No | Yes |

---

## Stage 0 — Prerequisites

```bash
pip install -r requirements-spsg.txt
python scripts/train_models.py        # pre-trains + caches the ensemble
```

Get free Alpaca paper-trading API keys at https://app.alpaca.markets (no
funding required for paper). Set them as environment variables:

```bash
export ALPACA_API_KEY="..."
export ALPACA_API_SECRET="..."
```

---

## Stage 1 — Generate a strategy

Via your running FastAPI app (after wiring in `api_strategy.py` per
`README_SPSG_INTEGRATION.md`):

```bash
curl -X POST http://127.0.0.1:8000/strategy/generate \
     -H "Authorization: Bearer $TOKEN"
```

Or directly in Python, useful for testing without spinning up the whole app:

```python
from spsg import SPSGEngine

engine = SPSGEngine()  # loads the cached ensemble, ~1s
strategy = engine.generate_strategy(survey_dict)   # survey_dict = a SurveyResponse row as a dict
print(strategy["strategy_name"], strategy["parameters"])
```

Save the result to a file — the CLI runner in Stage 3 reads from a JSON file
rather than hitting the API directly, so it can run as an unattended cron job
without needing a login session:

```python
import json
json.dump(strategy, open("strategy_user_1.json", "w"))
```

---

## Stage 2 — Rehearse: dashboard paper trading

Nothing to run here — this already works client-side in `dashboard.html`
(`placePaperTrade`, `checkSLTP`, etc.). Point the user at the Paper Trading
tab, let them watch the strategy's *parameters* (risk per trade, drawdown
limit, timeframe) inform how they size manual paper trades, or — if you want
the dashboard to actually reflect the generated strategy's live signal rather
than manual clicks — call `GET /strategy/me` on page load and use
`parameters.timeframe` / `parameters.direction` to auto-suggest trade sizing
in the Quick Paper Trade panel.

Minimum 30-day rehearsal period, per your business plan.

## Stage 3 — Broker-connected paper trading (the real test)

This is where `spsg/live_runner.py` and `scripts/run_live_strategy.py` come
in. Run one execution cycle manually first to sanity check:

```bash
python scripts/run_live_strategy.py \
    --user-id 1 \
    --symbols AAPL MSFT \
    --strategy-file strategy_user_1.json \
    --paper
```

What this does, in order, every time it's invoked:
1. Fetches the account's current portfolio value and open positions from Alpaca.
2. Checks the drawdown circuit breaker (`max_drawdown_target` from the strategy)
   against a persisted equity peak (`runtime_state/user_<id>_equity_peak.json`).
   If breached, flattens everything and stops — no new trades are placed
   until you re-run `/strategy/generate` and the user consents to resuming.
3. For each symbol, pulls the last 200 daily bars and computes the current
   moving-average-crossover signal using the *same* rule the backtester
   scored the strategy on (`holding_period_days` -> fast/slow MA pair).
4. Compares that signal to the currently-held position and does the minimum
   necessary: opens if flat and a signal exists, closes if flat is signalled,
   flips sides if the signal reversed, does nothing if already correctly
   positioned.
5. Position size = `account.portfolio_value * risk_per_trade_pct * leverage`,
   split evenly across up to `max_positions` symbols.

**Run this on a schedule, not continuously.** Pick a cadence: for a
swing-timeframe strategy (`holding_period_days` in the 5-20 range, which is
what most survey profiles will produce) once-daily after market close is
plenty; a shorter `holding_period_days` warrants more frequent checks (e.g.
hourly), but this rule-based MA-crossover strategy was backtested on daily
bars, so don't run it more often than the granularity it was validated on.

### Option A — cron (simplest, single-server deployments)

```cron
# Run at 21:15 UTC (after US market close), Mon-Fri
15 21 * * 1-5 cd /path/to/algosark_backend && \
  ALPACA_API_KEY=xxx ALPACA_API_SECRET=yyy \
  python3 scripts/run_live_strategy.py --user-id 1 --symbols AAPL MSFT \
  --strategy-file /path/to/strategy_user_1.json --paper >> /var/log/algosark/strategy.log 2>&1
```

For multiple users, generate one cron line per user (or wrap the loop in a
small shell/Python script that iterates over all active subscribers, pulling
each one's strategy JSON from the `strategies` DB table via `/strategy/me`
instead of a static file).

### Option B — APScheduler (if you're already running a long-lived Python process)

```python
from apscheduler.schedulers.background import BackgroundScheduler
from spsg.broker.alpaca_adapter import get_adapter_from_env
from spsg.live_runner import run_strategy_once
import json

scheduler = BackgroundScheduler(timezone="UTC")

def run_all_active_strategies():
    adapter = get_adapter_from_env()
    for user_id, strategy_json, symbols in get_active_subscriber_strategies():  # your own DB query
        strategy = json.loads(strategy_json)
        run_strategy_once(adapter, user_id=user_id, strategy=strategy, symbols=symbols)

scheduler.add_job(run_all_active_strategies, "cron", day_of_week="mon-fri", hour=21, minute=15)
scheduler.start()
```

### Option C — systemd timer (production servers, better observability than cron)

```ini
# /etc/systemd/system/algosark-strategy.service
[Unit]
Description=Algosark strategy execution cycle

[Service]
Type=oneshot
WorkingDirectory=/path/to/algosark_backend
EnvironmentFile=/etc/algosark/alpaca.env
ExecStart=/usr/bin/python3 scripts/run_live_strategy.py --user-id 1 --symbols AAPL MSFT --strategy-file /path/to/strategy_user_1.json --paper
```

```ini
# /etc/systemd/system/algosark-strategy.timer
[Unit]
Description=Run Algosark strategy daily after market close

[Timer]
OnCalendar=Mon-Fri 21:15 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now algosark-strategy.timer
journalctl -u algosark-strategy.service -f   # watch logs live
```

---

## Stage 4 — Monitor drift, gate any strategy changes on consent

On a slower schedule (e.g. weekly), call:

```bash
curl -X POST http://127.0.0.1:8000/strategy/{id}/check-drift -H "Authorization: Bearer $TOKEN"
```

If `needs_review: true`, **do not silently regenerate the strategy** — per
the business plan's "consent-based strategy updates" requirement, surface
this to the user (email / in-app notification) and only call
`/strategy/generate` again after they explicitly opt in. Wire this into the
dashboard by polling `/strategy/{id}/check-drift` on page load and showing a
banner when it flags `needs_review`.

---

## Stage 5 — Go live

Identical to Stage 3, except:
1. `/broker/connect` is called with `paper: false` (requires live Alpaca keys)
2. `run_live_strategy.py` is invoked with `--live` instead of `--paper`
3. **Currently blocked on purpose**: `api_strategy.py`'s `POST /broker/order`
   returns a 403 for any non-paper adapter, with a message about pending FCA
   Appointed-Representative compliance sign-off. Remove that guard only once
   that's actually in place — it's there as a safety rail, not a bug.

---

## Quick sanity checklist before trusting any of this with real money

- [ ] `scripts/train_models.py` has been re-run since the last time you edited
      `spsg/ensemble.py`'s rule-distillation logic
- [ ] `backtester.load_price_history`'s synthetic fallback has been replaced
      with real historical bars (`adapter.get_historical_bars`) everywhere
      backtests are shown to users
- [ ] `tests/test_integration.py` and `tests/test_live_runner.py` both pass
- [ ] You've watched `run_live_strategy.py --paper` run for at least the
      30-day minimum rehearsal period from the business plan before flipping
      any account to `--live`
- [ ] The 403 guard on live order placement is still in place until
      compliance sign-off is actually done
