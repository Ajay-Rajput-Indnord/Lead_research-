"""Send email over SMTP (Gmail, Outlook/Microsoft 365, or any SMTP provider)."""
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from config import (SENDER_NAME, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_SECURITY,
                    SMTP_USER)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MailError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM)


def valid_email(addr: str | None) -> bool:
    return bool(addr and EMAIL_RE.match(addr.strip()))


def send(to_addr: str, subject: str, body: str) -> str:
    """Send a plain-text email. Returns the Message-ID."""
    if not is_configured():
        raise MailError("Email sending is not set up. Fill the SMTP_* values in .env and restart.")
    if not valid_email(to_addr):
        raise MailError(f"'{to_addr}' is not a valid email address.")
    if not subject.strip() or not body.strip():
        raise MailError("Subject and body can't be empty.")

    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, SMTP_FROM)) if SENDER_NAME else SMTP_FROM
    msg["To"] = to_addr.strip()
    msg["Subject"] = subject.strip()
    msg["Message-ID"] = make_msgid(domain=SMTP_FROM.split("@")[-1])
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        if SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=context)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        with server:
            if SMTP_SECURITY == "starttls":
                server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("SMTP login failed. For Gmail use an App Password; for Microsoft 365 your "
                        "admin may need to enable SMTP AUTH for your mailbox.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"The server refused the recipient {to_addr}.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Sending failed: {exc}") from exc
    return msg["Message-ID"]
