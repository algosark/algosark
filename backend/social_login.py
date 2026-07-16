"""
Algosark auth backend — Google + GitHub OAuth.

This is a minimal, correct implementation of the *mechanics* of OAuth
(state verification, code exchange, token/id_token verification, session
cookie). Swap `create_or_get_user` for real DB calls before shipping.
"""

import os
import secrets
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
import secrets
from backend.main import SessionLocal
from models import User

from typing import Optional

db = SessionLocal()
load_dotenv()

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GITHUB_CLIENT_ID = os.environ["GITHUB_CLIENT_ID"]
GITHUB_CLIENT_SECRET = os.environ["GITHUB_CLIENT_SECRET"]
SESSION_SECRET = os.environ["SESSION_SECRET"]

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8000")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
COOKIE_HTTPS_ONLY = os.environ.get("COOKIE_HTTPS_ONLY", "false").lower() == "true"

GITHUB_REDIRECT_URI = "http://localhost:8000/auth/github/callback"

from fastapi import APIRouter

router = APIRouter(tags=["Social Login"])

# NOTE: same_site="lax" only works when frontend and backend share the same
# registrable domain (e.g. app.algosark.com + api.algosark.com behind the
# same eTLD+1, or a dev proxy). If frontend/backend are on totally different
# origins in production, use same_site="none" + https_only=True (requires HTTPS).

def create_or_get_user(db: Session, provider: str, provider_user_id: str, email: Optional[str], name: Optional[str] = None):
    
    user = db.query(User).filter(User.email == email).first()

    if user:
        return user

    names = (name or "").split(" ", 1)

    first_name = names[0] if names else ""
    last_name = names[1] if len(names) > 1 else ""

    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password_hash="",      # or None if nullable
        trading_experience="Beginner",
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


# --------------------------------------------------------------------------
# GitHub — full redirect flow (backend does everything)
# --------------------------------------------------------------------------

@router.get("/auth/github/login")
async def github_login(request: Request):
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
async def github_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login.html?error=github_denied")

    expected_state = request.session.pop("oauth_state", None)
    if not state or state != expected_state:
        raise HTTPException(400, "Invalid or missing OAuth state")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(400, f"GitHub token exchange failed: {token_data}")

        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user = user_res.json()

        email = user.get("email")
        if not email:
            emails_res = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            emails = emails_res.json()
            primary = next((e for e in emails if e.get("primary")), None)
            email = primary["email"] if primary else None

    account = create_or_get_user(db, "github", str(user["id"]), email, user.get("name") or user.get("login"))
    request.session["user"] = account

    return RedirectResponse(f"{FRONTEND_URL}/dashboard.html")


# --------------------------------------------------------------------------
# Google — popup code flow (frontend gets a one-time `code`, no secret exposed)
# --------------------------------------------------------------------------

class GoogleCodePayload(BaseModel):
    code: str


@router.post("/auth/google/callback")
async def google_callback(request: Request, payload: GoogleCodePayload):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": payload.code,
                "grant_type": "authorization_code",
                # Must be the literal string "postmessage" for the JS
                # popup code-client flow — no real redirect happens.
                "redirect_uri": "postmessage",
            },
        )
        token_data = token_res.json()
        id_token = token_data.get("id_token")
        if not id_token:
            raise HTTPException(400, f"Google token exchange failed: {token_data}")

        # For production, prefer the `google-auth` library's
        # id_token.verify_oauth2_token(...) over this endpoint (it verifies
        # the signature locally and isn't subject to Google's tokeninfo
        # rate limits).
        verify_res = await client.get(
            "https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token}
        )
        claims = verify_res.json()
        if claims.get("aud") != GOOGLE_CLIENT_ID:
            raise HTTPException(400, "Token audience mismatch")

    account = create_or_get_user(db, "google", claims["sub"], claims.get("email"), claims.get("name"))
    request.session["user"] = account

    return JSONResponse({"ok": True, "user": account})


# --------------------------------------------------------------------------
# Shared session endpoints
# --------------------------------------------------------------------------

@router.get("/auth/me")
async def me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}
