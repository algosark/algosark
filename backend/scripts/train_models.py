"""
scripts/train_models.py
========================
Pre-trains and caches the SPSG ensemble to disk (models_cache/ensemble/) so
that FastAPI app startup doesn't pay the ~20-30s training cost on every
restart/deploy. Run this once during your build/deploy step:

    python scripts/train_models.py

Re-run it whenever you update spsg/ensemble.py's rule-distillation logic, or
once real subscriber-outcome data is available and you swap
`generate_synthetic_training_set` for a real labelled dataset.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spsg.ensemble import SPSGEnsemble
from spsg.strategy_generator import MODEL_DIR


def main():
    print(f"Training SPSG ensemble -> caching to {MODEL_DIR / 'ensemble'}")
    t0 = time.time()
    ensemble = SPSGEnsemble.train(n_samples=4000)
    ensemble.save(MODEL_DIR / "ensemble")
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
