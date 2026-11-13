"""Opcjonalne powiadomienia SMTP dla nowych ofert."""
from __future__ import annotations
import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from typing import Mapping
from .sources import clean_url

class EmailError(Exception):
    pass

def send_new_jobs(offers: list[dict[str, str]], env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    host = env.get("SMTP_HOST", "").strip()
    username, password = env.get("SMTP_USERNAME", ""), env.get("SMTP_PASSWORD", "")
    sender = env.get("SMTP_FROM", "").strip()
    recipients = [x.strip() for x in env.get("SMTP_TO", "").split(",") if x.strip()]
    try:
        port = int(env.get("SMTP_PORT", "465"))
    except ValueError:
        raise EmailError("SMTP_PORT musi być numerem") from None
    security = env.get("SMTP_SECURITY", "ssl").strip().casefold()
    if not host or not sender or not recipients or not username or not password:
        raise EmailError("uzupełnij wymagane sekrety SMTP")
    if "\r" in sender or "\n" in sender or any("\r" in x or "\n" in x for x in recipients):
        raise EmailError("nieprawidłowy adres email")
    if not (1 <= port <= 65535) or security not in {"ssl", "starttls"}:
        raise EmailError("SMTP_PORT lub SMTP_SECURITY ma nieprawidłową wartość")

    safe_offers = []
    for offer in offers:
        source = str(offer.get("source", ""))
        url = clean_url(offer.get("url"), allow_http=source == "adzuna")
        title = str(offer.get("title", "")).strip()
        if not url or not title:
            raise EmailError("lista nowych ofert zawiera nieprawidłowy wpis")
        safe_offers.append({"title":title[:240],"company":str(offer.get("company",""))[:200],
            "location":str(offer.get("location",""))[:200],"source":source,"url":url})

    message = EmailMessage()
    message["Subject"] = f"Nowe oferty pracy ({len(safe_offers)})"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=False)
    lines = ["Nowe oferty dopasowane do Twojej konfiguracji:", ""]
    cards = ['<!doctype html><html lang="pl"><meta charset="utf-8"><body style="font-family:Arial,sans-serif;color:#18212b"><p>Nowe oferty dopasowane do Twojej konfiguracji:</p>']
    for offer in safe_offers:
        lines.extend((offer["title"],offer["company"],offer["location"],"Źródło: "+offer["source"],offer["url"]))
        if offer["source"] == "adzuna":
            lines.append("Adzuna: https://www.adzuna.pl/")
        lines.append("")
        title, company, location, url = (html.escape(offer[key], quote=True)
                                          for key in ("title","company","location","url"))
        cards.append('<section style="border:1px solid #dbe2e8;border-radius:8px;padding:14px;margin:12px 0">')
        cards.append(f"<strong>{title}</strong><p>{company}</p><p>{location}</p>")
        cards.append(f'<p><a rel="noopener" href="{url}">Otwórz ofertę</a></p>')
        if offer["source"] == "adzuna":
            cards.append('<a href="https://www.adzuna.pl/" rel="noopener" aria-label="Adzuna — źródło ofert pracy" style="display:inline-block;width:116px;min-width:116px;height:23px;line-height:23px;text-align:center">Adzuna</a>')
        cards.append("</section>")
    cards.append("</body></html>")
    message.set_content("\n".join(lines))
    message.add_alternative("".join(cards), subtype="html")
    try:
        if security == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context()) as client:
                client.login(username, password)
                refused = client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=25) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                client.login(username, password)
                refused = client.send_message(message)
    except (OSError, smtplib.SMTPException):
        raise EmailError("nie udało się wysłać wiadomości; kolejka pozostaje do ponowienia") from None
    if refused:
        raise EmailError("serwer SMTP nie przyjął wszystkich odbiorców; kolejka pozostaje do ponowienia")
    return f"wysłano {len(safe_offers)} ofert"
