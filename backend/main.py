from __future__ import annotations

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import DateTime, create_engine, Column, Integer, Float, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, Mapped, relationship
import bcrypt
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import declarative_base
from schema import SurveySubmit
from jose import JWTError, jwt
import json
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from social_login import router as social_router
from models import AuthToken, User
from database import engine, SessionLocal, Base
import broker_registry
from account_notifications import notify_admin_new_account
from auth_tokens import (
    create_access_token,
    create_action_token,
    decode_action_token,
    hash_action_token,
)
from email_service import is_configured, send_email
from sqlalchemy import inspect, text
import logging


# ====================== DATABASE SETUP ======================
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ["SESSION_SECRET"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
logger = logging.getLogger(__name__)

# ====================== FASTAPI APP ======================
app = FastAPI(title="Algosark API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "https://algosark.co.uk", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    same_site="lax",
    https_only=False,
)

# NOTE: tokenUrl must match the actual login route below. Your login route
# is /api/login, not /login — this was previously mismatched and was one of
# the causes of the 404/405 errors from earlier in this build. FastAPI only
# uses this value to populate the OpenAPI docs' "Authorize" flow; it doesn't
# affect token validation itself, but it should still point at the real route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/survey.html")
def serve_survey_page():
    return FileResponse("static/survey.html")

@app.get("/login.html")
def serve_login_page():
    return FileResponse("static/login.html")

@app.get("/index.html")
def serve_index_page():
    return FileResponse("static/index.html")

@app.get("/dashboard.html")
def serve_dashboard_page():
    return FileResponse("static/dashboard.html")

@app.get("/pricing.html")
def serve_pricing_page():
    return FileResponse("static/pricing.html")

@app.get("/register.html")
def serve_register_page():
    return FileResponse("static/register.html")

@app.get("/forgot-password.html")
def serve_forgot_password_page():
    return FileResponse("static/forgot-password.html")

@app.get("/reset-password.html")
def serve_reset_password_page():
    return FileResponse("static/reset-password.html")

@app.get("/verify-email.html")
def serve_verify_email_page():
    return FileResponse("static/verify-email.html")

@app.get("/admin-generate-strategy.html")
def serve_admin_generate_strategy_page():
    return FileResponse("static/admin-generate-strategy.html")


def ensure_auth_schema():
    if "email_verified" not in {column["name"] for column in inspect(engine).get_columns("users")}:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0"
            ))


Base.metadata.create_all(bind=engine)
ensure_auth_schema()


class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ==================== SECTION 1: Personal Profile ====================
    full_name = Column(String(150), nullable=False)
    email = Column(String(255), nullable=False)
    age_range = Column(String(20))
    country = Column(String(100))
    employment_status = Column(String(50))

    # ==================== SECTION 2: Trading Experience ====================
    trading_experience = Column(String(30))
    asset_classes = Column(Text)
    algo_familiarity = Column(String(50))
    coding_background = Column(String(50))
    trading_frequency = Column(String(30))

    # ==================== SECTION 3: Financial Profile ====================
    annual_income = Column(String(30))
    net_worth = Column(String(30))
    initial_capital = Column(String(30))
    debts = Column(String(50))
    emergency_fund = Column(String(50))

    # ==================== SECTION 4: Risk Tolerance ====================
    max_monthly_loss = Column(Float)
    reaction_to_20pct_loss = Column(String(50))
    return_profile = Column(String(50))
    investment_horizon = Column(String(30))
    past_loss_experience = Column(String(50))

    # ==================== SECTION 5: Strategy Preferences ====================
    target_markets = Column(Text)
    preferred_style = Column(String(50))
    excluded_sectors = Column(Text)
    leverage_preference = Column(String(50))
    long_short_preference = Column(String(30))

    # ==================== SECTION 6: Technical Preferences ====================
    brokerages = Column(Text)
    account_type = Column(String(50))
    api_comfort_level = Column(String(50))
    trading_sessions = Column(Text)
    overnight_weekend = Column(String(50))

    # ==================== SECTION 7: Goals & Constraints ====================
    primary_motivation = Column(String(100))
    target_annual_return = Column(String(30))
    max_simultaneous_positions = Column(String(20))
    additional_constraints = Column(Text)

    # ==================== Declarations ====================
    decl_risk_capital = Column(Boolean, default=False)
    decl_past_performance = Column(Boolean, default=False)
    decl_consent = Column(Boolean, default=False)
    decl_over_18 = Column(Boolean, default=False)

    user = relationship("User", back_populates="survey_response")

    def __repr__(self):
        return f"<SurveyResponse id={self.id} user_id={self.user_id}>"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ====================== PYDANTIC SCHEMAS ======================
class UserRegister(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    trading_experience: Optional[str] = None
    marketingOptIn: Optional[bool] = False


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: EmailStr
    trading_experience: Optional[str] = None
    created_at: str
    email_verified: bool = False


# Helper functions
def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def create_stored_action_token(db: Session, user_id: int, purpose: str, expiry: timedelta) -> str:
    token = create_action_token(user_id, purpose, expiry)
    db.add(AuthToken(
        user_id=user_id,
        token_hash=hash_action_token(token),
        purpose=purpose,
        expires_at=datetime.utcnow() + expiry,
    ))
    db.commit()
    return token


def send_verification_email(db: Session, user: User) -> None:
    token = create_stored_action_token(db, user.id, "verify_email", timedelta(days=2))
    link = f"{os.getenv('FRONTEND_URL', 'http://localhost:8000')}/verify-email.html#token={token}"
    send_email(user.email, "Confirm your Algosark email", f"Confirm your email address:\n\n{link}\n")


# ====================== AUTH ENDPOINTS ======================
@app.post("/api/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user: UserRegister, db: Session = Depends(get_db)):
    existing_user = get_user_by_email(db, user.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    if not is_configured():
        raise HTTPException(status_code=503, detail="Email delivery is not configured; account creation is unavailable.")

    hashed_password = get_password_hash(user.password)

    db_user = User(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        password_hash=hashed_password,
        trading_experience=user.trading_experience,
        marketing_opt_in=user.marketingOptIn,
        email_verified=False,
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    broker_registry.ensure_paper_adapter(db_user.id)
    try:
        send_verification_email(db, db_user)
        notify_admin_new_account(db_user)
    except Exception as exc:
        logger.exception("Email notification failed for new account %s", db_user.id)
        raise HTTPException(
            status_code=503,
            detail="Account created, but email delivery failed. Use the resend-verification option or contact support.",
        ) from exc
    return db_user


@app.post("/api/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = get_user_by_email(db, user.email)

    if not db_user or not db_user.password_hash or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    if not db_user.email_verified:
        raise HTTPException(status_code=403, detail="Confirm your email address before signing in.")

    access_token = create_access_token(db_user.email)
    broker_registry.ensure_paper_adapter(db_user.id)

    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "first_name": db_user.first_name,
            "last_name": db_user.last_name,
            "email": db_user.email,
            "trading_experience": db_user.trading_experience,
        },
    }


class EmailPayload(BaseModel):
    email: EmailStr


class PasswordResetPayload(BaseModel):
    token: str
    password: str


@app.post("/api/verification/resend")
def resend_verification(payload: EmailPayload, db: Session = Depends(get_db)):
    if not is_configured():
        raise HTTPException(status_code=503, detail="Email delivery is not configured.")
    user = get_user_by_email(db, payload.email)
    if user and not user.email_verified:
        send_verification_email(db, user)
    return {"message": "If the account needs verification, a confirmation email has been sent."}


class VerifyEmailPayload(BaseModel):
    token: str


@app.post("/api/verify-email")
def verify_email(payload: VerifyEmailPayload, db: Session = Depends(get_db)):
    token_hash = hash_action_token(payload.token)
    record = db.query(AuthToken).filter(
        AuthToken.token_hash == token_hash,
        AuthToken.purpose == "verify_email",
        AuthToken.used_at.is_(None),
        AuthToken.expires_at > datetime.utcnow(),
    ).first()
    if not record:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    record.used_at = datetime.utcnow()
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="The account for this link no longer exists.")
    user.email_verified = True
    db.commit()
    return {"verified": True}


@app.post("/api/password/forgot")
def forgot_password(payload: EmailPayload, db: Session = Depends(get_db)):
    from email_service import is_configured
    if not is_configured():
        raise HTTPException(status_code=503, detail="Email delivery is not configured.")
    user = get_user_by_email(db, payload.email)
    if user and user.password_hash:
        token = create_stored_action_token(db, user.id, "reset_password", timedelta(hours=1))
        link = f"{os.getenv('FRONTEND_URL', 'http://localhost:8000')}/reset-password.html#token={token}"
        send_email(user.email, "Reset your Algosark password", f"Reset your password using this link:\n\n{link}\n")
    return {"message": "If an account exists for that email, a password reset link has been sent."}


@app.post("/api/password/reset")
def reset_password(payload: PasswordResetPayload, db: Session = Depends(get_db)):
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    token_hash = hash_action_token(payload.token)
    record = db.query(AuthToken).filter(
        AuthToken.token_hash == token_hash,
        AuthToken.purpose == "reset_password",
        AuthToken.used_at.is_(None),
        AuthToken.expires_at > datetime.utcnow(),
    ).first()
    if not record:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="The account for this link no longer exists.")
    user.password_hash = get_password_hash(payload.password)
    record.used_at = datetime.utcnow()
    db.query(AuthToken).filter(
        AuthToken.user_id == user.id,
        AuthToken.purpose == "reset_password",
        AuthToken.used_at.is_(None),
    ).update({"used_at": datetime.utcnow()})
    db.commit()
    try:
        send_email(
            user.email,
            "Your Algosark password was changed",
            "Your Algosark password has been reset. If you did not make this change, contact support immediately.\n",
        )
    except Exception as exc:
        logger.exception("Password reset confirmation email failed for user %s", user.id)
        raise HTTPException(
            status_code=503,
            detail="Your password was changed, but the confirmation email could not be sent.",
        ) from exc
    return {"message": "Password reset successfully. You can now sign in."}


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not isinstance(email, str):
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = get_user_by_email(db, email)
    if user is None:
        raise credentials_exception

    broker_registry.ensure_paper_adapter(user.id)
    return user


@app.get("/users/me", response_model=UserResponse)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


# ====================== SURVEY ======================
@app.post("/survey/submit", status_code=201)
def submit_survey(
    survey: SurveySubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")

    current_user.survey_completed = True
    current_user.survey_submitted_at = datetime.utcnow()

    survey_data = survey.dict()

    list_fields = ["asset_classes", "target_markets", "brokerages", "trading_sessions"]
    for field in list_fields:
        if field in survey_data and isinstance(survey_data[field], list):
            survey_data[field] = json.dumps(survey_data[field])

    db_survey = SurveyResponse(user_id=current_user.id, **survey_data)

    db.add(db_survey)
    db.commit()
    db.refresh(db_survey)

    return {"message": "Survey submitted successfully!", "survey_id": db_survey.id}

@app.get("/")
def root():
    return FileResponse("static/index.html")

# ====================== SPSG STRATEGY ENGINE + BROKER + TRADING + PROFILE ======================
# All four of these are additive: they define their own tables (bound to the
# same Base above) and their own routers (built via factory functions that
# take the get_db/get_current_user/User/SurveyResponse/UserResponse objects
# already defined above), so nothing about the auth/survey code above needs
# to change.
from models_strategy import define_strategy_model
from models_trade import define_trade_model
from api_strategy import build_strategy_router, build_broker_router
from api_trading import build_trading_router
from api_profile import build_profile_router

Strategy = define_strategy_model(Base)
Trade = define_trade_model(Base)

# Must run again after Strategy/Trade are defined, so their tables actually
# get created — the first call only knows about User/SurveyResponse.
Base.metadata.create_all(bind=engine)

app.include_router(build_strategy_router(get_db, get_current_user, SurveyResponse, Strategy))
app.include_router(build_broker_router(get_db, get_current_user))
app.include_router(build_trading_router(get_db, get_current_user, Trade))
app.include_router(build_profile_router(get_db, get_current_user, UserResponse))
app.include_router(social_router)
