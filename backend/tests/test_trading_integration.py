"""
End-to-end test of api_trading.py + the broker_registry-backed broker router
in api_strategy.py. Uses a mock BrokerAdapter (no real Alpaca creds needed)
to prove the "broker-backed order -> persisted trade -> closeable -> priced
via quote" path works, then proves the synthetic fallback path works when no
broker is connected.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import StaticPool

import broker_registry
from models_trade import define_trade_model
from api_trading import build_trading_router
from spsg.broker.base import BrokerAdapter, BrokerAccount, BrokerPosition, BrokerOrder

DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True)


Trade = define_trade_model(Base)
Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == 1).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


app.include_router(build_trading_router(get_db, get_current_user, Trade))

client = TestClient(app)

db = SessionLocal()
db.add(User(id=1, email="tomiwa@algosark.co.uk"))
db.commit()
db.close()


# ----------------------------------------------------------------------------
# Mock broker adapter — deterministic prices, no network needed
# ----------------------------------------------------------------------------
class MockAdapter(BrokerAdapter):
    provider_name = "mock"

    def __init__(self):
        self._price = 100.0
        self._positions = {}

    def connect(self, api_key, api_secret, paper=True):
        self._is_paper = paper

    def get_account(self):
        return BrokerAccount("mock-acct", 10000.0, 10000.0, "USD", True)

    def get_positions(self):
        return list(self._positions.values())

    def place_order(self, symbol, qty, side, order_type="market", limit_price=None, stop_price=None):
        fill_price = self._price
        self._positions[symbol] = BrokerPosition(symbol, qty, "long" if side == "buy" else "short", fill_price, fill_price, 0.0)
        return BrokerOrder("mock-order-1", symbol, side, qty, order_type, "filled", filled_avg_price=fill_price)

    def close_position(self, symbol):
        self._price += 2.5  # simulate favourable price move before close
        pos = self._positions.pop(symbol, None)
        return BrokerOrder("mock-order-2", symbol, "sell", pos.qty if pos else 0, "market", "filled", filled_avg_price=self._price)

    def get_historical_bars(self, symbol, timeframe="1Day", limit=500):
        return [{"t": i, "o": self._price, "h": self._price, "l": self._price, "c": self._price} for i in range(min(limit, 10))]

    def get_latest_quote(self, symbol):
        return {"symbol": symbol, "bid": self._price - 0.1, "ask": self._price + 0.1, "price": self._price, "timestamp": None}


mock = MockAdapter()
mock.connect("fake", "fake", paper=True)
broker_registry.set_adapter(1, True, mock)  # paper mode has a "connected broker"

print("=== POST /trading/orders (paper, broker connected) ===")
resp = client.post("/trading/orders", json={"symbol": "AAPL", "side": "buy", "qty": 2, "mode": "paper"})
assert resp.status_code == 201, resp.text
trade = resp.json()
print(trade)
assert trade["source"] == "broker"
assert trade["entry_price"] == 100.0
assert trade["status"] == "open"
trade_id = trade["id"]

print("\n=== GET /trading/positions?mode=paper (should show unrealised P&L) ===")
resp = client.get("/trading/positions", params={"mode": "paper"})
assert resp.status_code == 200, resp.text
positions = resp.json()
print(positions)
assert len(positions) == 1
assert positions[0]["current_price"] == 100.0  # mock price hasn't moved yet

print("\n=== POST /trading/orders/{id}/close (mock adapter bumps price +2.5 on close) ===")
resp = client.post(f"/trading/orders/{trade_id}/close", json={"reason": "manual"})
assert resp.status_code == 200, resp.text
closed = resp.json()
print(closed)
assert closed["status"] == "closed"
assert closed["exit_price"] == 102.5
assert abs(closed["pnl"] - (2.5 * 2)) < 1e-6  # (102.5-100)*qty(2)*long(+1)

print("\n=== GET /trading/orders?mode=paper&status=closed ===")
resp = client.get("/trading/orders", params={"mode": "paper", "status": "closed"})
assert resp.status_code == 200, resp.text
history = resp.json()
print(history)
assert len(history) == 1
assert history[0]["id"] == trade_id

print("\n=== POST /trading/orders (live, NO live broker connected -> should 400) ===")
resp = client.post("/trading/orders", json={"symbol": "AAPL", "side": "buy", "qty": 1, "mode": "live"})
print(resp.status_code, resp.json())
assert resp.status_code == 400

print("\n=== Connect a LIVE mock adapter, retry live order ===")
mock_live = MockAdapter()
mock_live.connect("fake", "fake", paper=False)
broker_registry.set_adapter(1, False, mock_live)
resp = client.post("/trading/orders", json={"symbol": "TSLA", "side": "buy", "qty": 1, "mode": "live"})
assert resp.status_code == 201, resp.text
live_trade = resp.json()
print(live_trade)
assert live_trade["mode"] == "live"
assert live_trade["source"] == "broker"

print("\n=== POST /trading/orders (paper, NO broker connected for a DIFFERENT user -> synthetic fallback) ===")
db = SessionLocal()
db.add(User(id=2, email="second@algosark.co.uk"))
db.commit()
db.close()

def get_current_user_2(db: Session = Depends(get_db)):
    return db.query(User).filter(User.id == 2).first()

app.dependency_overrides[get_current_user] = get_current_user_2
resp = client.post("/trading/orders", json={"symbol": "GBPUSD", "side": "buy", "qty": 1, "mode": "paper"})
assert resp.status_code == 201, resp.text
synth_trade = resp.json()
print(synth_trade)
assert synth_trade["source"] == "synthetic"
app.dependency_overrides.pop(get_current_user, None)

print("\n=== GET /trading/bars (synthetic fallback shape check for user 2) ===")
app.dependency_overrides[get_current_user] = get_current_user_2
resp = client.get("/trading/bars", params={"symbol": "GBPUSD", "timeframe": "5m", "mode": "paper", "limit": 20})
assert resp.status_code == 200, resp.text
bars_resp = resp.json()
print("source:", bars_resp["source"], "num_bars:", len(bars_resp["bars"]))
assert bars_resp["source"] == "synthetic"
assert len(bars_resp["bars"]) == 20
app.dependency_overrides.pop(get_current_user, None)

print("\n=== GET /trading/bars (broker source for user 1, timeframe=1h) ===")
resp = client.get("/trading/bars", params={"symbol": "AAPL", "timeframe": "1h", "mode": "paper"})
assert resp.status_code == 200, resp.text
bars_resp = resp.json()
print("source:", bars_resp["source"], "num_bars:", len(bars_resp["bars"]))
assert bars_resp["source"] == "broker"

print("\nALL TRADING INTEGRATION CHECKS PASSED")
