import logging

from app.config import settings

log = logging.getLogger("student-auth.email")


def email_enabled() -> bool:
    if settings.email_backend == "console":
        return True
    return bool(settings.resend_api_key and settings.email_from)


def send_password_reset_email(
    recipient_email: str,
    reset_url: str,
) -> None:
    if settings.email_backend == "console":
        # Development only (enforced in config): there is no email provider, so the link is written to the service log.
        log.warning("[DEV EMAIL] password reset link for %s: %s", recipient_email, reset_url)
        return

    import resend

    resend.api_key = settings.resend_api_key

    resend.Emails.send(
        {
            "from": settings.email_from,
            "to": [recipient_email],
            "subject": "Reset your password",
            "html": f"""
                <p>Hello,</p>
                <p>We received a request to reset your password.</p>
                <p>
                    <a href="{reset_url}">Reset your password</a>
                </p>
                <p>This link expires in 30 minutes.</p>
                <p>If you did not request this, you can ignore this email.</p>
            """,
        }
    )
