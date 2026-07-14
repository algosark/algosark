"""
scripts/run_live_strategy.py
==============================
CLI entrypoint that runs ONE strategy-execution cycle for one user, then
exits. Designed to be invoked on a schedule (cron / systemd timer / APScheduler
— see HOW_TO_RUN_A_STRATEGY.md) rather than run as a long-lived process: a
schedule-and-exit design is easier to reason about, monitor, and restart
safely than a persistent trading loop.

Usage:
    python scripts/run_live_strategy.py --user-id 1 --symbols AAPL MSFT \
        --strategy-file strategy.json --paper

Environment variables (or pass --api-key / --api-secret directly):
    ALPACA_API_KEY, ALPACA_API_SECRET
"""

import sys
import argparse
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spsg.broker.alpaca_adapter import AlpacaAdapter
from spsg.live_runner import run_strategy_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_live_strategy")


def main():
    parser = argparse.ArgumentParser(description="Run one SPSG strategy-execution cycle.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--symbols", nargs="+", required=True, help="Broker-tradeable tickers, e.g. AAPL MSFT")
    parser.add_argument("--strategy-file", type=str, required=True,
                         help="Path to a JSON file containing one strategy payload "
                              "(the same shape returned by GET /strategy/me)")
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--api-secret", type=str, default=None)
    parser.add_argument("--paper", action="store_true", default=True)
    parser.add_argument("--live", dest="paper", action="store_false")
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("ALPACA_API_KEY")
    api_secret = args.api_secret or os.environ.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        logger.error("No Alpaca credentials supplied (env ALPACA_API_KEY/ALPACA_API_SECRET or --api-key/--api-secret)")
        sys.exit(1)

    strategy = json.loads(Path(args.strategy_file).read_text())

    adapter = AlpacaAdapter()
    adapter.connect(api_key, api_secret, paper=args.paper)

    logger.info("Running strategy '%s' for user %s on %s (paper=%s)",
                strategy.get("strategy_name"), args.user_id, args.symbols, args.paper)

    results = run_strategy_once(adapter, user_id=args.user_id, strategy=strategy, symbols=args.symbols)

    for r in results:
        logger.info("%s: signal=%s target=%s action=%s qty=%s %s",
                     r.symbol, r.signal, r.target_position, r.action_taken, r.qty, r.notes)

    n_actions = sum(1 for r in results if r.action_taken not in ("none",))
    logger.info("Cycle complete: %d/%d symbols acted on", n_actions, len(results))


if __name__ == "__main__":
    main()
