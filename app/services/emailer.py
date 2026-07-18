import smtplib
from email.message import EmailMessage

from app import config


def send_email(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        print(f"\n=== EMAIL (SMTP not configured, printing) ===\n"
              f"To: {to}\nSubject: {subject}\n\n{body}\n=== END EMAIL ===\n")
        return

    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        if config.SMTP_USERNAME:
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def send_status_change(to: str, monitor, previous: str, new: str, changed_at) -> None:
    subject = f"Permit {monitor.permit_number} status changed: {new}"
    body = (
        f"Permit status changed\n\n"
        f"{monitor.permit_number}\n"
        f"{monitor.address}\n"
        f"{monitor.description}\n\n"
        f"Previous status: {previous or '(none recorded)'}\n"
        f"New status: {new}\n"
        f"Changed: {changed_at.strftime('%B %d at %-I:%M %p')} UTC\n\n"
        f"View on official city portal:\n{monitor.portal_url}\n\n"
        f"Permit Ping will continue monitoring this permit.\n"
        f"Manage your permits: {config.BASE_URL}/account\n"
    )
    send_email(to, subject, body)


def send_account_link(to: str, token: str) -> None:
    link = f"{config.BASE_URL}/account?t={token}"
    send_email(
        to,
        "Your Permit Ping account link",
        f"Manage your monitored permits here:\n\n{link}\n\n"
        f"This link is good for {config.LOGIN_TOKEN_DAYS} days.\n",
    )


def send_monitor_started(to: str, monitor, token: str) -> None:
    link = f"{config.BASE_URL}/account?t={token}"
    send_email(
        to,
        f"Now monitoring permit {monitor.permit_number}",
        f"Permit Ping is now monitoring:\n\n"
        f"{monitor.permit_number}\n"
        f"{monitor.address}\n"
        f"Current status: {monitor.current_status}\n\n"
        f"We check the official record daily and email you when it changes.\n\n"
        f"Manage your permits: {link}\n",
    )
