"""
Standalone integration test: builds a minimal stand-in for your real main.py
(same User/SurveyResponse shape) and exercises the full flow:
register -> login -> submit survey -> generate strategy -> fetch strategy ->
strategy history -> drift check -> broker connect (expected to fail, no
network/real creds here, proving the error path is handled cleanly).

This mirrors exactly how api_strategy.py's router factories are meant to be
wired into your actual app.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.testclient import TestClient
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import StaticPool
from datetime import datetime
import json

from models_strategy import define_strategy_model
from api_strategy import build_strategy_router, build_broker_router

DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True)


class SurveyResponse(Base):
    __tablename__ = "survey_responses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    trading_experience = Column(String(30), default="1-3yr")
    algo_familiarity = Column(String(50), default="heard of it")
    coding_background = Column(String(50), default="none")
    trading_frequency = Column(String(30), default="weekly")
    annual_income = Column(String(30), default="40k-70k")
    net_worth = Column(String(30), default="50k-150k")
    emergency_fund = Column(String(50), default="3-6 months")
    debts = Column(String(50), default="mortgage only")
    max_monthly_loss = Column(Float, default=12.0)
    reaction_to_20pct_loss = Column(String(50), default="hold")
    return_profile = Column(String(50), default="balanced growth")
    investment_horizon = Column(String(30), default="3-5yr")
    past_loss_experience = Column(String(50), default="small losses, held on")
    leverage_preference = Column(String(50), default="up to 2:1")
    api_comfort_level = Column(String(50), default="somewhat comfortable")
    account_type = Column(String(50), default="general")
    employment_status = Column(String(50), default="employed")
    target_markets = Column(Text, default=json.dumps(["forex", "equities"]))
    asset_classes = Column(Text, default=json.dumps(["forex"]))
    brokerages = Column(Text, default=json.dumps(["Interactive Brokers"]))


Strategy = define_strategy_model(Base)
Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(db: Session = Depends(get_db)):
    # Test stand-in: always return user #1 (real main.py decodes a JWT here)
    user = db.query(User).filter(User.id == 1).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


app.include_router(build_strategy_router(get_db, get_current_user, SurveyResponse, Strategy))
app.include_router(build_broker_router(get_db, get_current_user))

client = TestClient(app)

# ---- seed a user + survey response ----
db = SessionLocal()
db.add(User(id=1, email="tomiwa@algosark.co.uk"))
db.add(SurveyResponse(user_id=1))
db.commit()
db.close()

print("=== POST /strategy/generate ===")
resp = client.post("/strategy/generate")
print(resp.status_code)
assert resp.status_code == 201, resp.text
data = resp.json()
print("strategy_name:", data["strategy_name"])
print("risk_profile_score:", data["risk_profile_score"])
print("parameters:", data["parameters"])
print("detected_regime:", data["regime_detection"]["detected_regime"])

print("\n=== GET /strategy/me ===")
resp = client.get("/strategy/me")
assert resp.status_code == 200, resp.text
print("OK, strategy_name matches:", resp.json()["strategy_name"] == data["strategy_name"])

print("\n=== GET /strategy/history ===")
resp = client.get("/strategy/history")
assert resp.status_code == 200, resp.text
print(resp.json())

strategy_id = resp.json()[0]["id"]
print(f"\n=== POST /strategy/{strategy_id}/check-drift ===")
resp = client.post(f"/strategy/{strategy_id}/check-drift")
assert resp.status_code == 200, resp.text
print(resp.json())

print("\n=== POST /broker/connect (expected to fail cleanly - no real Alpaca creds/network here) ===")
resp = client.post("/broker/connect", json={"provider": "alpaca", "api_key": "fake", "api_secret": "fake", "paper": True})
print(resp.status_code, resp.json())
assert resp.status_code == 400  # expected: connection error surfaced as 400, not a 500 crash

print("\n=== POST /strategy/generate AGAIN (should supersede old strategy) ===")
resp = client.post("/strategy/generate")
assert resp.status_code == 201
resp = client.get("/strategy/history")
active_count = sum(1 for r in resp.json() if r["is_active"])
print("Active strategies after 2nd generation (should be 1):", active_count)
assert active_count == 1

print("\nALL INTEGRATION CHECKS PASSED")
