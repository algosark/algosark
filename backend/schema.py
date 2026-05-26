from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class SurveySubmit(BaseModel):
    # SECTION 1: Personal Profile
    full_name: str
    email: EmailStr
    age_range: Optional[str] = None
    country: Optional[str] = None
    employment_status: Optional[str] = None

    # SECTION 2: Trading Experience
    trading_experience: Optional[str] = None
    asset_classes: Optional[List[str]] = None
    algo_familiarity: Optional[str] = None
    coding_background: Optional[str] = None
    trading_frequency: Optional[str] = None

    # SECTION 3: Financial Profile
    annual_income: Optional[str] = None
    net_worth: Optional[str] = None
    initial_capital: Optional[str] = None
    debts: Optional[str] = None
    emergency_fund: Optional[str] = None

    # SECTION 4: Risk Tolerance
    max_monthly_loss: Optional[float] = None
    reaction_to_20pct_loss: Optional[str] = None
    return_profile: Optional[str] = None
    investment_horizon: Optional[str] = None
    past_loss_experience: Optional[str] = None

    # SECTION 5: Strategy Preferences
    target_markets: Optional[List[str]] = None
    preferred_style: Optional[str] = None
    excluded_sectors: Optional[str] = None
    leverage_preference: Optional[str] = None
    long_short_preference: Optional[str] = None

    # SECTION 6: Technical Preferences
    brokerages: Optional[List[str]] = None
    account_type: Optional[str] = None
    api_comfort_level: Optional[str] = None
    trading_sessions: Optional[List[str]] = None
    overnight_weekend: Optional[str] = None

    # SECTION 7: Goals & Constraints
    primary_motivation: Optional[str] = None
    target_annual_return: Optional[str] = None
    max_simultaneous_positions: Optional[str] = None
    additional_constraints: Optional[str] = None

    # Declarations
    decl_risk_capital: bool = False
    decl_past_performance: bool = False
    decl_consent: bool = False
    decl_over_18: bool = False

    class Config:
        from_attributes = True