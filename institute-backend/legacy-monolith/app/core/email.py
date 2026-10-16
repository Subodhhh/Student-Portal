"""Pluggable email delivery. The production provider is not chosen yet: SMTP is
generic and configured purely through environment variables."""
import smtplib
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.config import settings


@dataclass
class Mail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, mail: Mail) -> None: ...


class ConsoleEmailSender:
    """DEV ONLY (startup validation rejects it in qa/prod). Prints to stderr,
    deliberately not through logging, so it can never reach log aggregation."""

    def send(self, mail: Mail) -> None:
        print(f"[dev-email] to={mail.to} subject={mail.subject}\n{mail.body}", file=sys.stderr)


class SmtpEmailSender:
    def send(self, mail: Mail) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.email_from, mail.to, mail.subject
        msg.set_content(mail.body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)


class MemoryEmailSender:
    """Test double that records messages."""

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.outbox.append(mail)


_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    global _sender
    if _sender is None:
        _sender = SmtpEmailSender() if settings.email_backend == "smtp" else ConsoleEmailSender()
    return _sender


def set_sender(sender: EmailSender | None) -> None:
    global _sender
    _sender = sender
