from sqlalchemy import DateTime, create_engine, Column, Integer, Float, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker, Session, Mapped, relationship



Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255))
    trading_experience = Column(Text, nullable=True)  # e.g., "2 years", "Beginner", etc.
    marketing_opt_in = Column(Integer, default=0)  # 0 = No, 1 = Yes
    survey_completed = Column(Boolean, default=False)
    survey_submitted_at = Column(DateTime, nullable=True)
    google_id = Column(String, unique=True, nullable=True)
    github_id = Column(String, unique=True, nullable=True)
    provider = Column(String, nullable=True)
    survey_response = relationship("SurveyResponse", back_populates="user", uselist=False)
    created_at = Column(String(50), default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(
        String(50),
        default=lambda: datetime.utcnow().isoformat(),
        onupdate=lambda: datetime.utcnow().isoformat(),
    )