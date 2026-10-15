from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


class EmailConfigurationError(RuntimeError):
    pass


def is_configured() -> bool:
    return all((
        os.getenv("SMTP_HOST"),
        os.getenv("SMTP_USERNAME"),
        os.getenv("SMTP_PASSWORD"),
        os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_USERNAME"),
    ))


def send_email(recipient: str, subject: str, text: str) -> None:
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM_EMAIL") or username
    if not is_configured():
        raise EmailConfigurationError(
            "Email delivery is not configured. Set SMTP_HOST, SMTP_USERNAME, "
            "SMTP_PASSWORD, and SMTP_FROM_EMAIL in the environment."
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)

    context = ssl.create_default_context()
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    port = int(os.getenv("SMTP_PORT", "465" if use_ssl else "587"))
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
