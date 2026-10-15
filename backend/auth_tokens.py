from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from dotenv import load_dotenv
from jose import JWTError, jwt

load_dotenv()
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET", "")
ALGORITHM = "HS256"


def create_access_token(email: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=60))
    return jwt.encode({"sub": email, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def create_action_token(
    user_id: int,
    purpose: str,
    expires_in: timedelta,
    email: str | None = None,
) -> str:
    claims = {
        "sub": str(user_id),
        "purpose": purpose,
        "nonce": secrets.token_urlsafe(16),
        "exp": datetime.utcnow() + expires_in,
    }
    if email:
        claims["email"] = email
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def decode_action_token_claims(token: str, purpose: str) -> tuple[int, str | None]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != purpose:
            raise ValueError("Invalid token purpose")
        email = payload.get("email")
        return int(payload["sub"]), email if isinstance(email, str) else None
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("This link is invalid or has expired.") from exc


def decode_action_token(token: str, purpose: str) -> int:
    user_id, _ = decode_action_token_claims(token, purpose)
    return user_id


def hash_action_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
