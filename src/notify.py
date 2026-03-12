"""Send email notifications to PMs when invoices are ready."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from .config_loader import load_pm_assignments, CONFIG_DIR, BASE_DIR


def send_pm_notification(pm_key, invoice_count, coding_url=None):
    """Send an email to a PM notifying them of invoices to code."""
    config = load_pm_assignments()
    email_config = config.get("email", {})

    if not email_config.get("enabled"):
        return {"sent": False, "reason": "Email not enabled in config"}

    pm_info = config["project_managers"].get(pm_key)
    if not pm_info or not pm_info.get("email"):
        return {"sent": False, "reason": f"No email configured for {pm_key}"}

    sender_email = email_config["sender_email"]
    # Password from environment variable (never stored in config)
    sender_password = os.environ.get("SMTP_PASSWORD", "")
    if not sender_password:
        # Try .env file
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("SMTP_PASSWORD="):
                    sender_password = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not sender_password:
        return {"sent": False, "reason": "SMTP_PASSWORD not set in environment or .env file"}

    # Build email
    date_str = datetime.now().strftime("%m/%d/%Y")
    subject = email_config.get("subject_template", "Invoices Ready for Coding - {date}").format(
        date=date_str
    )

    body_template = email_config.get("body_template",
        "Hi {pm_name},\n\nYou have {invoice_count} invoice(s) ready for coding.\n\n"
        "Click here to code them:\n{coding_url}\n\nThanks")
    body = body_template.format(
        pm_name=pm_info["name"],
        invoice_count=invoice_count,
        coding_url=coding_url or "",
        date=date_str,
    )

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = pm_info["email"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(email_config.get("smtp_server", "smtp.office365.com"),
                              email_config.get("smtp_port", 587))
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return {"sent": True, "to": pm_info["email"]}
    except Exception as e:
        return {"sent": False, "reason": str(e)}


def notify_all_pms(distribution_results):
    """Send notifications to all PMs that received invoices."""
    config = load_pm_assignments()
    app_url = config.get("email", {}).get("app_url", "")
    results = {}

    pm_links = distribution_results.get("pm_links", {})

    for pm_name, file_path in distribution_results.get("pm_files", {}).items():
        # Find PM key by name
        pm_key = None
        for key, info in config["project_managers"].items():
            if info["name"] == pm_name:
                pm_key = key
                break

        if pm_key:
            token = pm_links.get(pm_name, "")
            coding_url = f"{app_url}/code/{token}" if app_url and token else ""
            result = send_pm_notification(pm_key, invoice_count=1, coding_url=coding_url)
            results[pm_name] = result

    return results
