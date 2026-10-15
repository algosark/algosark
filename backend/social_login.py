from __future__ import annotations

import os
import secrets
import logging
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import broker_registry
from account_notifications import notify_admin_new_account, send_welcome_email
from auth_tokens import create_access_token
from database import SessionLocal
from email_service import is_configured
from models import User

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
GITHUB_REDIRECT_URI = os.getenv(
    "GITHUB_REDIRECT_URI", f"{BACKEND_URL}/auth/github/callback"
)

router = APIRouter(tags=["Social Login"])
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def user_response(user: User) -> dict:
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "email_verified": user.email_verified,
    }


def create_or_get_user(
    db: Session,
    provider: str,
    provider_user_id: str,
    email: Optional[str],
    name: Optional[str] = None,
) -> tuple[User, bool, bool]:
    if not email:
        raise HTTPException(400, "The provider did not return an email address.")

    provider_column = User.github_id if provider == "github" else User.google_id
    user = db.query(User).filter(provider_column == provider_user_id).first()
    if user:
        user.email_verified = True
        db.commit()
        return user, False, False

    user = db.query(User).filter(User.email == email).first()
    created = user is None
    if user is None:
        names = (name or "").split(" ", 1)
        user = User(
            first_name=names[0] if names else "",
            last_name=names[1] if len(names) > 1 else "",
            email=email,
            password_hash="",
            trading_experience="Beginner",
        )
        db.add(user)

    if provider == "github":
        user.github_id = provider_user_id
    else:
        user.google_id = provider_user_id
    user.provider = provider
    user.email_verified = True
    db.commit()
    db.refresh(user)
    broker_registry.ensure_paper_adapter(user.id)

    notification_warning = False
    if created:
        if not is_configured():
            notification_warning = True
            logger.error("SMTP is not configured; signup emails were not sent for account %s", user.id)
        else:
            for notification in (notify_admin_new_account, send_welcome_email):
                try:
                    notification(user)
                except Exception:
                    notification_warning = True
                    logger.exception("Signup email notification failed for account %s", user.id)
    return user, created, notification_warning


@router.get("/auth/config")
async def auth_config():
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
        "github_login_url": f"{BACKEND_URL}/auth/github/login",
        "google_enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "github_enabled": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
    }


@router.get("/auth/github/login")
async def github_login(request: Request):
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(503, "GitHub sign-in is not configured.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    }
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login.html?error=github_denied")
    phase = "state validation"
    try:
        expected_state = request.session.pop("oauth_state", None)
        if not state or state != expected_state:
            raise HTTPException(400, "Invalid or missing OAuth state")
        if not code:
            raise HTTPException(400, "GitHub did not return an authorization code.")
        if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
            raise HTTPException(503, "GitHub sign-in is not configured.")

        phase = "GitHub code exchange"
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": GITHUB_REDIRECT_URI,
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                logger.error("GitHub token exchange returned no access token")
                raise HTTPException(400, "GitHub token exchange failed; restart sign-in and try again.")

            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            phase = "GitHub profile lookup"
            user_response = await client.get("https://api.github.com/user", headers=headers)
            user_response.raise_for_status()
            provider_user = user_response.json()

            phase = "GitHub verified-email lookup"
            email_response = await client.get("https://api.github.com/user/emails", headers=headers)
            email_response.raise_for_status()
            emails = email_response.json()
            primary = next(
                (item for item in emails if item.get("primary") and item.get("verified")),
                None,
            )
            email = primary["email"] if primary else None

        phase = "account persistence"
        account, is_new, notification_warning = create_or_get_user(
            db,
            "github",
            str(provider_user["id"]),
            email,
            provider_user.get("name") or provider_user.get("login"),
        )
        token = create_access_token(account.email, timedelta(days=30))
        next_page = "survey" if is_new else "dashboard"
        warning = "&notification_warning=true" if notification_warning else ""
        return RedirectResponse(
            f"{FRONTEND_URL}/dashboard.html#access_token={token}&next={next_page}{warning}"
        )
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.exception("GitHub OAuth callback failed during %s", phase)
        return RedirectResponse(
            f"{FRONTEND_URL}/login.html?error=github_callback_failed"
        )


class GoogleCodePayload(BaseModel):
    code: str


@router.post("/auth/google/callback")
async def google_callback(
    payload: GoogleCodePayload,
    db: Session = Depends(get_db),
):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google sign-in is not configured.")
    async with httpx.AsyncClient(timeout=15) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": payload.code,
                "grant_type": "authorization_code",
                "redirect_uri": "postmessage",
            },
        )
        token_response.raise_for_status()
        id_token = token_response.json().get("id_token")
        if not id_token:
            raise HTTPException(400, "Google token exchange failed.")

        verification = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
        )
        verification.raise_for_status()
        claims = verification.json()
        if claims.get("aud") != GOOGLE_CLIENT_ID:
            raise HTTPException(400, "Token audience mismatch")
        if claims.get("email_verified") not in (True, "true"):
            raise HTTPException(400, "Google must confirm the email address before sign-in.")

    account, is_new, notification_warning = create_or_get_user(
        db, "google", claims["sub"], claims.get("email"), claims.get("name")
    )
    return {
        "ok": True,
        "user": user_response(account),
        "access_token": create_access_token(account.email, timedelta(days=30)),
        "is_new": is_new,
        "notification_warning": notification_warning,
    }
