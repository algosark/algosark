"""
models_trade.py
================
Defines the `Trade` table — every order a user places, paper or live, gets a
row here. This is what makes the merged Trade tab's "Recent Orders" /
Positions / History views real instead of resetting on every page refresh.

Usage in main.py, same factory pattern as models_strategy.py:

    from models_trade import define_trade_model
    Trade = define_trade_model(Base)
    Base.metadata.create_all(bind=engine)
"""

from datetime import datetime


def define_trade_model(Base):
    from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
    from sqlalchemy.orm import relationship

    class Trade(Base):
        __tablename__ = "trades"

        id = Column(Integer, primary_key=True, index=True)
        user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

        mode = Column(String(10), nullable=False, index=True)     # "paper" | "live"
        symbol = Column(String(20), nullable=False, index=True)
        side = Column(String(10), nullable=False)                  # "buy" | "sell"
        order_type = Column(String(10), default="market")

        qty = Column(Float, nullable=False)
        entry_price = Column(Float, nullable=False)
        exit_price = Column(Float, nullable=True)
        sl = Column(Float, nullable=True)
        tp = Column(Float, nullable=True)

        status = Column(String(10), default="open", index=True)   # "open" | "closed"
        pnl = Column(Float, nullable=True)

        # Populated when the order actually went through a connected broker
        # adapter rather than the synthetic-price fallback (see api_trading.py).
        broker_order_id = Column(String(64), nullable=True)
        source = Column(String(10), default="synthetic")           # "broker" | "synthetic"

        close_reason = Column(String(50), nullable=True)           # "manual" | "stop_loss" | "take_profit"

        opened_at = Column(DateTime, default=datetime.utcnow)
        closed_at = Column(DateTime, nullable=True)

        def __repr__(self):
            return f"<Trade id={self.id} user_id={self.user_id} {self.symbol} {self.side} {self.status}>"

    return Trade
