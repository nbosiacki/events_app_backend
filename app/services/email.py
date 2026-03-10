"""
Email service for sending transactional emails.

Currently configured for Gmail SMTP via aiosmtplib.
NOTE: Gmail has a 500/day send limit. Switch to a dedicated provider
(Resend, SendGrid, Postmark) before wider deployment.
"""
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import get_settings


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """
    Send a password reset email.

    Args:
        to_email: Recipient email address.
        reset_url: Full URL including reset token (e.g. https://events.nicholasbosiacki.com/reset-password?token=...)
    """
    settings = get_settings()
    if not settings.smtp_user or not settings.smtp_password:
        # Email not configured — skip silently (dev mode)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset your password — Events App"
    msg["From"] = settings.from_email or settings.smtp_user
    msg["To"] = to_email

    text_body = f"Click the link below to reset your password:\n\n{reset_url}\n\nThis link expires in 1 hour."
    html_body = f"""
    <p>Click the link below to reset your password:</p>
    <p><a href="{reset_url}">{reset_url}</a></p>
    <p>This link expires in 1 hour. If you didn't request a reset, ignore this email.</p>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )
