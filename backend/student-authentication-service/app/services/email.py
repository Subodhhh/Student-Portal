import resend

from app.config import settings


def send_password_reset_email(
    recipient_email: str,
    reset_url: str,
) -> None:
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