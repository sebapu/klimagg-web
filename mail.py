"""KlimaGG-Web — SMTP mail service.
Version: v2.0.0

Technical implementation and configuration use English identifiers. User-facing subjects and message bodies intentionally remain German because the website product language is German. SMTP transport uses STARTTLS when configured.
"""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from config import settings

__version__ = "2.0.0"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _first_nonempty(*values: object) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _project_name() -> str:
    return _clean(getattr(settings, "PROJECT_NAME", "")) or "Webprojekt"


def _smtp_transport_config() -> tuple[str, int, bool, str, str, int]:
    host = _clean(getattr(settings, "SMTP_HOST", ""))
    username = _clean(getattr(settings, "SMTP_USERNAME", ""))
    password = str(getattr(settings, "SMTP_PASSWORD", "") or "")
    if not host:
        raise RuntimeError("SMTP_HOST ist nicht konfiguriert.")
    if bool(username) != bool(password):
        raise RuntimeError("SMTP_USERNAME und SMTP_PASSWORD müssen gemeinsam gesetzt oder gemeinsam leer sein.")
    port = int(getattr(settings, "SMTP_PORT", 587) or 587)
    use_tls = bool(getattr(settings, "SMTP_USE_TLS", True))
    timeout = int(getattr(settings, "SMTP_TIMEOUT_SECONDS", 20) or 20)
    return host, port, use_tls, username, password, timeout


def validate_magic_link_mail_config() -> None:
    """Validate the minimum production mail configuration before creating a login token."""
    _smtp_transport_config()
    sender = _first_nonempty(
        getattr(settings, "MAIL_LOGIN_ADDRESS", ""),
        getattr(settings, "MAIL_INFO_ADDRESS", ""),
    )
    if not sender:
        raise RuntimeError("MAIL_LOGIN_ADDRESS oder MAIL_INFO_ADDRESS muss konfiguriert sein.")


def _contact_recipient() -> str:
    return _first_nonempty(
        getattr(settings, "MAIL_CONTACT_FORM_TO", ""),
        getattr(settings, "MAIL_KONTAKT_ADDRESS", ""),
        getattr(settings, "MAIL_INFO_ADDRESS", ""),
    )


def _interest_recipient() -> str:
    return _first_nonempty(
        getattr(settings, "MAIL_INTEREST_FORM_TO", ""),
        _contact_recipient(),
    )


def _datenschutz_recipient() -> str:
    return _first_nonempty(
        getattr(settings, "MAIL_DATENSCHUTZ_FORM_TO", ""),
        getattr(settings, "MAIL_DATENSCHUTZ_ADDRESS", ""),
        getattr(settings, "MAIL_INFO_ADDRESS", ""),
    )


def _admin_recipient() -> str:
    return _first_nonempty(
        getattr(settings, "MAIL_ADMIN_ALERT_TO", ""),
        getattr(settings, "MAIL_ADMIN_ADDRESS", ""),
        getattr(settings, "MAIL_INFO_ADDRESS", ""),
    )


def _make_plain_fallback_from_html(html: str) -> str:
    """Create a deliberately simple plain-text fallback from HTML mail content."""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "Bitte HTML-Ansicht aktivieren."


def send_mail(
    *,
    to_address: str,
    subject: str,
    html_body: Optional[str] = None,
    text_body: Optional[str] = None,
    from_address: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> None:
    """Send a generic text and/or HTML e-mail.
    
    If both bodies are provided, the message uses `multipart/alternative`. HTML-only messages receive a generated plain-text fallback. `reply_to` is optional.
    
    Raises `ValueError` for missing message content or addresses and lets SMTP transport/authentication errors propagate.
    """
    if not text_body and not html_body:
        raise ValueError("send_mail: mindestens text_body oder html_body muss gesetzt sein.")

    recipient = _clean(to_address)
    sender = _first_nonempty(from_address, getattr(settings, "MAIL_INFO_ADDRESS", ""))
    if not recipient:
        raise ValueError("send_mail: Empfängeradresse fehlt.")
    if not sender:
        raise RuntimeError("send_mail: Absenderadresse ist nicht konfiguriert.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    reply_to_clean = _clean(reply_to)
    if reply_to_clean:
        msg["Reply-To"] = reply_to_clean

    if text_body and html_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
    elif text_body:
        msg.set_content(text_body)
    else:
        plain = _make_plain_fallback_from_html(html_body or "")
        msg.set_content(plain)
        msg.add_alternative(html_body or "", subtype="html")

    host, port, use_tls, username, password, timeout = _smtp_transport_config()
    context = ssl.create_default_context()

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        if use_tls:
            server.starttls(context=context)
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(msg)


def send_magic_link_mail(user_email: str, magic_link: str) -> None:
    """Send the German user-facing magic-link login message."""
    project_name = _project_name()
    subject = f"Dein {project_name}-Anmeldelink"
    safe_magic_link_attr = html.escape(str(magic_link or ""), quote=True)
    safe_magic_link_text = html.escape(str(magic_link or ""), quote=False)
    html_body = f"""
    <p>Hallo,</p>
    <p>hier ist dein Anmeldelink für {html.escape(project_name)}:</p>
    <p><a href="{safe_magic_link_attr}">{safe_magic_link_text}</a></p>
    <p>Der Link ist zeitlich begrenzt gültig. Wenn du diese Mail nicht angefordert hast,
    kannst du sie einfach ignorieren.</p>
    """

    send_mail(
        to_address=user_email,
        subject=subject,
        html_body=html_body,
        from_address=_first_nonempty(settings.MAIL_LOGIN_ADDRESS, settings.MAIL_INFO_ADDRESS),
        reply_to=_clean(settings.MAIL_INFO_ADDRESS),
    )


def send_contact_form_mail(user_email: str, message: str) -> None:
    """Forward a contact-form message to the configured contact recipient."""
    subject = f"{_project_name()} Kontaktformular"
    safe_email = html.escape(user_email or "", quote=True)
    safe_msg = html.escape(message or "", quote=True)
    html_body = f"""
    <p><b>Absender:</b> {safe_email}</p>
    <p><b>Nachricht:</b></p>
    <pre style="white-space:pre-wrap">{safe_msg}</pre>
    """
    text_body = f"Absender: {user_email}\n\nNachricht:\n{message}\n"

    send_mail(
        to_address=_contact_recipient(),
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        from_address=_first_nonempty(settings.MAIL_INFO_ADDRESS, settings.MAIL_LOGIN_ADDRESS),
        reply_to=user_email,
    )


def send_interest_form_mail(
    *,
    name: str,
    user_email: str,
    interest_type: str,
    message: str,
    organization: str = "",
    role: str = "",
) -> None:
    """Forward a general contact/interest request. Association interest is only one optional category."""
    to_addr = _interest_recipient()
    subject_hint = (interest_type or "Kontakt").strip()[:80]
    subject = f"{_project_name()} Kontakt & Interesse: {subject_hint}"

    safe_name = html.escape(name or "", quote=True)
    safe_email = html.escape(user_email or "", quote=True)
    safe_org = html.escape(organization or "", quote=True)
    safe_role = html.escape(role or "", quote=True)
    safe_interest = html.escape(interest_type or "", quote=True)
    safe_msg = html.escape(message or "", quote=True)

    html_body = f"""
    <p><b>Name:</b> {safe_name}</p>
    <p><b>E-Mail:</b> {safe_email}</p>
    <p><b>Organisation:</b> {safe_org or "—"}</p>
    <p><b>Rolle/Funktion:</b> {safe_role or "—"}</p>
    <p><b>Interessensbereich:</b> {safe_interest}</p>
    <p><b>Nachricht:</b></p>
    <pre style="white-space:pre-wrap">{safe_msg}</pre>
    """
    text_body = (
        f"Name: {name}\n"
        f"E-Mail: {user_email}\n"
        f"Organisation: {organization or '—'}\n"
        f"Rolle/Funktion: {role or '—'}\n"
        f"Interessensbereich: {interest_type}\n\n"
        f"Nachricht:\n{message}\n"
    )

    send_mail(
        to_address=to_addr,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        from_address=_first_nonempty(settings.MAIL_INFO_ADDRESS, settings.MAIL_LOGIN_ADDRESS),
        reply_to=user_email,
    )


def send_datenschutz_form_mail(user_email: str, message: str) -> None:
    """Forward a German GDPR/privacy request to the configured privacy recipient."""
    to_addr = _datenschutz_recipient()
    subject = f"{_project_name()} Datenschutz-Anfrage"
    safe_email = html.escape(user_email or "", quote=True)
    safe_message = html.escape(message or "", quote=True)
    html_body = f"""
    <p><b>Absender:</b> {safe_email}</p>
    <p><b>Nachricht:</b></p>
    <pre style="white-space:pre-wrap">{safe_message}</pre>
    """
    text_body = f"Absender: {user_email}\n\nNachricht:\n{message}\n"

    send_mail(
        to_address=to_addr,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        from_address=_first_nonempty(settings.MAIL_INFO_ADDRESS, settings.MAIL_LOGIN_ADDRESS),
        reply_to=user_email,
    )


def send_admin_alert(subject: str, body: str) -> None:
    """Send a simple operator/admin notification to the configured admin recipient."""
    html_body = f"<pre style='white-space:pre-wrap'>{html.escape(body or '', quote=True)}</pre>"

    send_mail(
        to_address=_admin_recipient(),
        subject=subject,
        html_body=html_body,
        text_body=body,
        from_address=_first_nonempty(settings.MAIL_NOREPLY_ADDRESS, settings.MAIL_INFO_ADDRESS, settings.MAIL_LOGIN_ADDRESS),
        reply_to=_clean(settings.MAIL_INFO_ADDRESS),
    )
