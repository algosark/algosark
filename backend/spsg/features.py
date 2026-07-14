"""
spsg/features.py
================
Feature Engineering layer of the Survey-Driven Personalised Strategy Generator (SPSG).

Converts the raw SurveyResponse row (as captured by /survey/submit in main.py) into:
  1. A dict of interpretable, human-readable "risk & readiness scores" (used for
     the strategy explanation shown on the 'My Strategy' dashboard page).
  2. A fixed-length numeric feature vector ("investor profile embedding") that is
     fed into the hybrid ensemble in ensemble.py.

Every mapping table below is intentionally explicit and editable — these are the
knobs a compliance/quant reviewer will want to tune, so we keep them as simple
dicts rather than hiding them inside the model.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import json
import numpy as np

# ----------------------------------------------------------------------------
# Ordinal encodings for categorical survey answers.
# Values run low -> high (low risk/readiness -> high risk/readiness).
# Update these if your survey.py option strings differ.
# ----------------------------------------------------------------------------

TRADING_EXPERIENCE_SCALE = {
    "none": 0, "beginner": 0,
    "<1yr": 1, "0-1yr": 1,
    "1-3yr": 2,
    "3-5yr": 3,
    "5+yr": 4, "5+ years": 4,
}

ALGO_FAMILIARITY_SCALE = {
    "never heard of it": 0,
    "heard of it": 1,
    "used a template": 2,
    "built my own": 3,
}

CODING_BACKGROUND_SCALE = {
    "none": 0,
    "basic (excel/no-code)": 1,
    "basic": 1,
    "intermediate": 2,
    "advanced": 3,
}

TRADING_FREQUENCY_SCALE = {
    "rarely": 0,
    "monthly": 1,
    "weekly": 2,
    "daily": 3,
    "intraday": 4,
}

INCOME_BAND_SCALE = {
    "<20k": 0, "20k-40k": 1, "40k-70k": 2, "70k-100k": 3, "100k+": 4,
}

NET_WORTH_BAND_SCALE = {
    "<10k": 0, "10k-50k": 1, "50k-150k": 2, "150k-500k": 3, "500k+": 4,
}

EMERGENCY_FUND_SCALE = {
    "none": 0, "<3 months": 1, "3-6 months": 2, "6+ months": 3,
}

DEBT_SCALE = {
    "significant unsecured debt": 0,
    "some debt": 1,
    "mortgage only": 2,
    "none": 3,
}

REACTION_TO_20PCT_LOSS_SCALE = {
    "sell everything": 0,
    "sell some": 1,
    "hold": 2,
    "buy more": 3,
}

RETURN_PROFILE_SCALE = {
    "capital preservation": 0,
    "steady income": 1,
    "balanced growth": 2,
    "aggressive growth": 3,
}

INVESTMENT_HORIZON_SCALE = {
    "<1yr": 0, "1-3yr": 1, "3-5yr": 2, "5yr+": 3,
}

PAST_LOSS_EXPERIENCE_SCALE = {
    "never invested": 0,
    "small losses, panicked": 1,
    "small losses, held on": 2,
    "large losses, learned from it": 3,
}

LEVERAGE_PREFERENCE_SCALE = {
    "none": 0, "up to 2:1": 1, "up to 5:1": 2, "up to 10:1": 3, "10:1+": 4,
}

API_COMFORT_SCALE = {
    "not comfortable": 0, "somewhat comfortable": 1, "comfortable": 2, "very comfortable": 3,
}

ACCOUNT_TYPE_SCALE = {
    "isa": 0, "general": 1, "sipp": 1, "margin": 2, "professional": 3,
}

EMPLOYMENT_STATUS_SCALE = {
    "unemployed": 0, "student": 0, "employed": 1, "self-employed": 2, "retired": 1,
}


def _score(value: Any, table: dict, default: int = 1) -> int:
    """Case-insensitive lookup into an ordinal scale table with a safe default."""
    if value is None:
        return default
    key = str(value).strip().lower()
    return table.get(key, default)


def _parse_list_field(value: Any) -> list[str]:
    """asset_classes / target_markets / brokerages / trading_sessions are stored
    as JSON strings in SQLite (see main.py's list_fields handling on submit).
    This undoes that for feature engineering purposes."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: comma-separated string
    return [v.strip() for v in str(value).split(",") if v.strip()]


@dataclass
class InvestorProfile:
    """Interpretable scores (0-1 normalised) shown to the user / used for rule
    fallbacks, plus the raw embedding vector fed into the ML ensemble."""

    risk_capacity: float          # ability to bear loss (financial cushion)
    risk_tolerance: float         # psychological willingness to bear loss
    experience_score: float       # trading + coding + algo familiarity
    automation_readiness: float   # api comfort + coding background
    time_horizon_score: float     # investment horizon
    return_ambition: float        # target return / return profile
    overall_risk_score: int       # 0-100, shown on the dashboard as "Risk Profile (xx/100)"
    embedding: np.ndarray = field(repr=False)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["embedding"] = self.embedding.tolist()
        return d


def build_investor_profile(survey: dict) -> InvestorProfile:
    """
    survey: a dict of the SurveyResponse row's columns (as returned by
    `row.__dict__` or a Pydantic `.dict()` of SurveySubmit).

    Returns an InvestorProfile with both human-readable scores and the
    numeric embedding used by the ensemble.
    """

    trading_experience   = _score(survey.get("trading_experience"), TRADING_EXPERIENCE_SCALE, 1)
    algo_familiarity     = _score(survey.get("algo_familiarity"), ALGO_FAMILIARITY_SCALE, 0)
    coding_background     = _score(survey.get("coding_background"), CODING_BACKGROUND_SCALE, 0)
    trading_frequency     = _score(survey.get("trading_frequency"), TRADING_FREQUENCY_SCALE, 1)

    annual_income          = _score(survey.get("annual_income"), INCOME_BAND_SCALE, 1)
    net_worth              = _score(survey.get("net_worth"), NET_WORTH_BAND_SCALE, 1)
    emergency_fund         = _score(survey.get("emergency_fund"), EMERGENCY_FUND_SCALE, 1)
    debts                  = _score(survey.get("debts"), DEBT_SCALE, 1)

    max_monthly_loss        = float(survey.get("max_monthly_loss") or 5.0)  # percent, slider
    reaction_to_20pct_loss   = _score(survey.get("reaction_to_20pct_loss"), REACTION_TO_20PCT_LOSS_SCALE, 1)
    return_profile           = _score(survey.get("return_profile"), RETURN_PROFILE_SCALE, 1)
    investment_horizon       = _score(survey.get("investment_horizon"), INVESTMENT_HORIZON_SCALE, 1)
    past_loss_experience     = _score(survey.get("past_loss_experience"), PAST_LOSS_EXPERIENCE_SCALE, 1)

    leverage_preference   = _score(survey.get("leverage_preference"), LEVERAGE_PREFERENCE_SCALE, 0)
    api_comfort           = _score(survey.get("api_comfort_level"), API_COMFORT_SCALE, 1)
    account_type          = _score(survey.get("account_type"), ACCOUNT_TYPE_SCALE, 1)
    employment_status     = _score(survey.get("employment_status"), EMPLOYMENT_STATUS_SCALE, 1)

    target_markets    = _parse_list_field(survey.get("target_markets"))
    asset_classes     = _parse_list_field(survey.get("asset_classes"))
    brokerages        = _parse_list_field(survey.get("brokerages"))

    # ---- Composite (0-1 normalised) scores ------------------------------
    risk_capacity = np.clip(
        0.30 * (net_worth / 4)
        + 0.25 * (annual_income / 4)
        + 0.25 * (emergency_fund / 3)
        + 0.20 * (debts / 3),
        0, 1,
    )

    risk_tolerance = np.clip(
        0.40 * (reaction_to_20pct_loss / 3)
        + 0.25 * (1 - min(max_monthly_loss, 30) / 30)  # note: inverted below in scoring
        + 0.20 * (return_profile / 3)
        + 0.15 * (past_loss_experience / 3),
        0, 1,
    )
    # max_monthly_loss the user is willing to tolerate is actually a *risk appetite*
    # input (higher = more risk tolerant), so correct the sign:
    risk_tolerance = np.clip(
        0.40 * (reaction_to_20pct_loss / 3)
        + 0.25 * (min(max_monthly_loss, 30) / 30)
        + 0.20 * (return_profile / 3)
        + 0.15 * (past_loss_experience / 3),
        0, 1,
    )

    experience_score = np.clip(
        0.5 * (trading_experience / 4) + 0.3 * (algo_familiarity / 3) + 0.2 * (trading_frequency / 4),
        0, 1,
    )

    automation_readiness = np.clip(
        0.5 * (api_comfort / 3) + 0.5 * (coding_background / 3),
        0, 1,
    )

    time_horizon_score = investment_horizon / 3.0

    return_ambition = np.clip(0.6 * (return_profile / 3) + 0.4 * (leverage_preference / 4), 0, 1)

    overall_risk_score = int(round(
        100 * np.clip(
            0.35 * risk_tolerance + 0.30 * risk_capacity + 0.20 * experience_score + 0.15 * return_ambition,
            0, 1,
        )
    ))

    # ---- Raw embedding vector for the ML ensemble ------------------------
    # Fixed order/length so trained models stay compatible across retrains.
    embedding = np.array([
        trading_experience / 4, algo_familiarity / 3, coding_background / 3, trading_frequency / 4,
        annual_income / 4, net_worth / 4, emergency_fund / 3, debts / 3,
        max_monthly_loss / 30, reaction_to_20pct_loss / 3, return_profile / 3,
        investment_horizon / 3, past_loss_experience / 3,
        leverage_preference / 4, api_comfort / 3, account_type / 3, employment_status / 2,
        len(target_markets) / 5, len(asset_classes) / 5, len(brokerages) / 3,
        risk_capacity, risk_tolerance, experience_score, automation_readiness,
        time_horizon_score, return_ambition,
    ], dtype=np.float64)

    return InvestorProfile(
        risk_capacity=float(risk_capacity),
        risk_tolerance=float(risk_tolerance),
        experience_score=float(experience_score),
        automation_readiness=float(automation_readiness),
        time_horizon_score=float(time_horizon_score),
        return_ambition=float(return_ambition),
        overall_risk_score=overall_risk_score,
        embedding=embedding,
    )


EMBEDDING_LENGTH = 26  # keep in sync with the vector above; used by ensemble.py at load time
