from __future__ import annotations

import os
from datetime import timedelta

from auth_tokens import create_action_token
from email_service import send_email


def notify_admin_new_account(user) -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "oluwadaretomiwa@gmail.com")
    token = create_action_token(
        user.id, "admin_generate_strategy", timedelta(days=30), email=user.email
    )
    link = (
        f"{os.getenv('FRONTEND_URL', 'http://localhost:8000')}"
        f"/admin-generate-strategy.html?token={token}"
    )
    send_email(
        admin_email,
        f"New Algosark account: {user.email}",
        f"A new account was created and is awaiting a personalized strategy.\n\n"
        f"Name: {user.first_name} {user.last_name}\nEmail: {user.email}\nUser ID: {user.id}\n\n"
        f"Generate the strategy after the user completes the risk survey:\n{link}\n",
    )


def notify_admin_strategy_request(user, token: str) -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "oluwadaretomiwa@gmail.com")
    link = (
        f"{os.getenv('FRONTEND_URL', 'http://localhost:8000')}"
        f"/admin-generate-strategy.html?token={token}"
    )
    send_email(
        admin_email,
        f"Strategy requested: {user.email}",
        f"The user has completed their survey and requested a personalized strategy.\n\n"
        f"Name: {user.first_name} {user.last_name}\nEmail: {user.email}\nUser ID: {user.id}\n\n"
        f"Generate the strategy:\n{link}\n",
    )


def send_welcome_email(user) -> None:
    send_email(
        user.email,
        "Welcome to Algosark",
        f"Your Algosark account is ready. Sign in at "
        f"{os.getenv('FRONTEND_URL', 'http://localhost:8000')}/login.html.\n",
    )
