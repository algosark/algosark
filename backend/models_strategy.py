"""
models_strategy.py
===================
Defines the `Strategy` table used to persist each user's generated SPSG
strategy. Implemented as a factory function so it can bind to the *same*
`Base` your existing main.py already declares, instead of creating a second,
disconnected declarative base.

Usage in main.py:

    from models_strategy import define_strategy_model
    Strategy = define_strategy_model(Base)
    Base.metadata.create_all(bind=engine)   # call again after defining Strategy
"""

from datetime import datetime


def define_strategy_model(Base):
    from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON
    from sqlalchemy.orm import relationship

    class Strategy(Base):
        __tablename__ = "strategies"

        id = Column(Integer, primary_key=True, index=True)
        user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

        strategy_name = Column(String(150), nullable=False)
        risk_profile_score = Column(Integer, nullable=False)
        detected_regime = Column(String(30), nullable=True)

        # Full generate_strategy() output (parameters, backtest results,
        # regime probabilities, investor profile, drift monitoring, disclaimer).
        payload = Column(JSON, nullable=False)

        is_active = Column(Integer, default=1)  # 1 = current strategy, 0 = superseded
        created_at = Column(DateTime, default=datetime.utcnow)

        def __repr__(self):
            return f"<Strategy id={self.id} user_id={self.user_id} name={self.strategy_name!r}>"

    return Strategy
