"""
finrag.deliver.email — SMTP delivery (STARTTLS).

Sends a multipart/alternative message (plain-text + HTML). Credentials come
from .env (Gmail app password, or any SMTP provider). send_email() returns
True on success, raising nothing the caller can't handle — the pipeline records
delivery_status either way.

A dry_run mode writes the email to stdout/file instead of sending, so the full
pipeline is testable without live SMTP.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from finrag.config import settings


def send_email(subject: str, html_body: str, text_body: str,
               *, to: str | None = None, dry_run: bool = False) -> bool:
    recipient = to or settings.email_to
    if not recipient:
        raise ValueError("no recipient: set EMAIL_TO in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user or "finrag@localhost"
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if dry_run:
        print(f"[dry_run] would send to {recipient}: {subject}")
        return True

    if not (settings.smtp_user and settings.smtp_password):
        raise ValueError("SMTP_USER / SMTP_PASSWORD not set in .env")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as srv:
        srv.starttls()
        srv.login(settings.smtp_user, settings.smtp_password)
        srv.sendmail(msg["From"], [recipient], msg.as_string())
    return True
