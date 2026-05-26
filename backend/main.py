from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import DateTime, create_engine, Column, Integer,Float, String, Text, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, Mapped, relationship
import bcrypt
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Mapped, mapped_column
from schema import SurveySubmit
from jose import JWTError, jwt

# ====================== DATABASE SETUP ======================
DATABASE_URL = "sqlite:///./users.db"
SECRET_KEY = "your-super-secret-key-change-this"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
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
    survey_response = relationship("SurveyResponse", back_populates="user", uselist=False)
    created_at = Column(String(50), default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(String(50), default=lambda: datetime.utcnow().isoformat(), onupdate=lambda: datetime.utcnow().isoformat())

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()

    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    return encoded_jwt

class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ==================== SECTION 1: Personal Profile ====================
    full_name = Column(String(150), nullable=False)
    email = Column(String(255), nullable=False)
    age_range = Column(String(20))                    # e.g., "25-34"
    country = Column(String(100))
    employment_status = Column(String(50))           # employed, self-employed, etc.

    # ==================== SECTION 2: Trading Experience ====================
    trading_experience = Column(String(30))           # none, <1yr, 1-3yr, etc.
    asset_classes = Column(Text)                      # JSON string or comma-separated
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
    max_monthly_loss = Column(Float)                  # from slider (percentage)
    reaction_to_20pct_loss = Column(String(50))
    return_profile = Column(String(50))
    investment_horizon = Column(String(30))
    past_loss_experience = Column(String(50))

    # ==================== SECTION 5: Strategy Preferences ====================
    target_markets = Column(Text)                     # JSON or comma-separated
    preferred_style = Column(String(50))
    excluded_sectors = Column(Text)                   # textarea
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

    # Relationship
    user = relationship("User", back_populates="survey_response")

    def __repr__(self):
        return f"<SurveyResponse id={self.id} user_id={self.user_id}>"

Base.metadata.create_all(bind=engine)

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
    id: int
    first_name: str
    last_name: str
    email: EmailStr
    trading_experience: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True

# ====================== FASTAPI APP ======================
app = FastAPI(title="Trading User Auth API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Helper functions
def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()

# ====================== ENDPOINTS ======================

@app.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user: UserRegister, db: Session = Depends(get_db)):
    # Check if user already exists
    existing_user = get_user_by_email(db, user.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash password
    hashed_password = get_password_hash(user.password)
    
    # Create new user
    db_user = User(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        password_hash=hashed_password,
        trading_experience=user.trading_experience
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return db_user


@app.post("/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = get_user_by_email(db, user.email)

    if not db_user or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    access_token = create_access_token(
        data={"sub": db_user.email}
    )

    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "first_name": db_user.first_name,
            "last_name": db_user.last_name,
            "email": db_user.email,
            "trading_experience": db_user.trading_experience
        }
    }

@app.get("/users/me", response_model=UserResponse)
def get_current_user(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db)
    ):
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

    return user

# Check if user is authenticated
@app.get("/is-authenticated", response_model=UserResponse)
def is_authenticated(token: str = Depends(oauth2_scheme)):
    # This is a simplified version. In production, decode JWT token to get user email/id
    # For demo purposes, we'll assume token is the email (not secure!)
    return token
# Simple health check

@app.post("/survey/submit", status_code=201)
def submit_survey( survey: SurveySubmit, db: Session = Depends(get_db), current_user = Depends(get_current_user) ):

    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    current_user.survey_completed = True
    current_user.survey_submitted_at = datetime.utcnow()    

    db_survey = SurveyResponse(
        user_id=current_user.id,
        **survey.dict()
    )
    
    db.add(db_survey)
    db.commit()
    db.refresh(db_survey)
    
    return {
        "message": "Survey submitted successfully!",
        "survey_id": db_survey.id
    }

@app.get("/")
def root():
    return {"message": "Trading User Auth API is running"}