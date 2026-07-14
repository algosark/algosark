"""
schema.py
=========
Pydantic request model for POST /api/survey. Every field here maps 1:1 onto
an assignable column on the `SurveyResponse` SQLAlchemy model in main.py
(everything except `id`, `user_id`, `created_at`, `updated_at`, and the
`user` relationship, which main.py sets separately) — this is what lets
`submit_survey()` do `SurveyResponse(user_id=current_user.id, **survey.dict())`
without a field-by-field mapping step.

This is also exactly what spsg/features.py's build_investor_profile() expects
as input (survey.py's SurveyResponse row -> dict -> build_investor_profile),
so keep the two in sync if you add/rename a survey question.
"""

from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class SurveySubmit(BaseModel):
    # ==================== SECTION 1: Personal Profile ====================
    full_name: str
    email: str
    age_range: Optional[str] = None            # e.g. "25-34"
    country: Optional[str] = "UK"
    employment_status: Optional[str] = None     # employed, self-employed, student, retired, unemployed

    # ==================== SECTION 2: Trading Experience ====================
    trading_experience: Optional[str] = None    # none, <1yr, 1-3yr, 3-5yr, 5+yr
    asset_classes: List[str] = Field(default_factory=list)
    algo_familiarity: Optional[str] = None       # never heard of it, heard of it, used a template, built my own
    coding_background: Optional[str] = None      # none, basic, intermediate, advanced
    trading_frequency: Optional[str] = None       # rarely, monthly, weekly, daily, intraday

    # ==================== SECTION 3: Financial Profile ====================
    annual_income: Optional[str] = None          # bucketed, e.g. "40k-70k"
    net_worth: Optional[str] = None
    initial_capital: Optional[str] = None
    debts: Optional[str] = None                  # none, mortgage only, some debt, significant unsecured debt
    emergency_fund: Optional[str] = None          # none, <3 months, 3-6 months, 6+ months

    # ==================== SECTION 4: Risk Tolerance ====================
    max_monthly_loss: Optional[float] = None     # percentage, from a slider
    reaction_to_20pct_loss: Optional[str] = None  # sell everything, sell some, hold, buy more
    return_profile: Optional[str] = None           # capital preservation, steady income, balanced growth, aggressive growth
    investment_horizon: Optional[str] = None       # <1yr, 1-3yr, 3-5yr, 5yr+
    past_loss_experience: Optional[str] = None

    # ==================== SECTION 5: Strategy Preferences ====================
    target_markets: List[str] = Field(default_factory=list)   # e.g. ["forex", "equities"]
    preferred_style: Optional[str] = None                       # scalp, day, swing, position
    excluded_sectors: Optional[str] = None                       # free text
    leverage_preference: Optional[str] = None                     # none, up to 2:1, up to 5:1, ...
    long_short_preference: Optional[str] = None                   # long_only, long_short

    # ==================== SECTION 6: Technical Preferences ====================
    brokerages: List[str] = Field(default_factory=list)
    account_type: Optional[str] = None            # isa, general, sipp, margin, professional
    api_comfort_level: Optional[str] = None         # not comfortable, somewhat comfortable, comfortable, very comfortable
    trading_sessions: List[str] = Field(default_factory=list)
    overnight_weekend: Optional[str] = None

    # ==================== SECTION 7: Goals & Constraints ====================
    primary_motivation: Optional[str] = None
    target_annual_return: Optional[str] = None
    max_simultaneous_positions: Optional[str] = None
    additional_constraints: Optional[str] = None

    # ==================== Declarations ====================
    decl_risk_capital: bool = False
    decl_past_performance: bool = False
    decl_consent: bool = False
    decl_over_18: bool = False
