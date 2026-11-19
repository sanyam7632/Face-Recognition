import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import get_settings


def send_unknown_alert(recipient: str, label: str, image_path: str) -> None:
    settings = get_settings()
    if not settings.smtp_username or not settings.smtp_password:
        return
    message = EmailMessage()
    message["Subject"] = f"Face Recognition alert: {label} detected"
    message["From"] = settings.smtp_from or settings.smtp_username
    message["To"] = recipient
    message.set_content(
        f"An unrecognized person ({label}) remained in view for more than "
        f"{settings.unknown_hold_seconds:g} seconds. Open Face Recognition to review the sighting."
    )
    path = Path(image_path)
    if path.exists():
        message.add_attachment(path.read_bytes(), maintype="image", subtype="jpeg", filename=path.name)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)

