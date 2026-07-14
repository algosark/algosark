"""
api_profile.py
===============
Adds PATCH /users/me so the dashboard's Profile page can actually persist
changes server-side (previously it only wrote to localStorage).

Wire-up in main.py (same pattern as api_strategy.py — a router factory that
takes your existing dependencies, no circular imports):

    from api_profile import build_profile_router
    app.include_router(build_profile_router(get_db, get_current_user, UserResponse))
"""

from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import Optional


class ProfileUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    trading_experience: Optional[str] = None


def build_profile_router(get_db, get_current_user, UserResponse) -> APIRouter:
    router = APIRouter(tags=["profile"])

    @router.patch("/users/me", response_model=UserResponse)
    def update_profile(
        body: ProfileUpdateRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        if body.first_name is not None:
            current_user.first_name = body.first_name
        if body.last_name is not None:
            current_user.last_name = body.last_name
        if body.email is not None:
            current_user.email = body.email
        if body.trading_experience is not None:
            current_user.trading_experience = body.trading_experience

        db.add(current_user)
        db.commit()
        db.refresh(current_user)
        return current_user

    return router
