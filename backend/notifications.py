"""Notification senders for Nodeglow – Telegram, Discord, Email."""
import asyncio
import logging
import smtplib
import ssl
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

async def _send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        resp.raise_for_status()


async def _send_discord(webhook_url: str, title: str, message: str, color: int = 0xe74c3c) -> None:
    payload = {"embeds": [{"title": title, "description": message, "color": color}]}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()


async def _send_email(host: str, port: int, user: str, password: str,
                      from_addr: str, to_addr: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    ctx = ssl.create_default_context()
    loop = asyncio.get_event_loop()
    def _send():
        with smtplib.SMTP_SSL(host, port, context=ctx) if port == 465 else smtplib.SMTP(host, port) as s:
            if port != 465:
                s.starttls(context=ctx)
            s.login(user, password)
            s.sendmail(from_addr, [to_addr], msg.as_string())
    await loop.run_in_executor(None, _send)


# ── Public API ────────────────────────────────────────────────────────────────

async def notify(title: str, message: str, severity: str = "critical") -> None:
    """Send notification to all configured channels. Fire-and-forget safe."""
    from database import AsyncSessionLocal, decrypt_value, get_setting
    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "notify_enabled", "0")
        if enabled != "1":
            return

        tg_token    = await get_setting(db, "telegram_bot_token", "")
        tg_chat     = await get_setting(db, "telegram_chat_id", "")
        dc_webhook  = await get_setting(db, "discord_webhook_url", "")
        smtp_host   = await get_setting(db, "smtp_host", "")
        smtp_port   = await get_setting(db, "smtp_port", "587")
        smtp_user   = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_from   = await get_setting(db, "smtp_from", "")
        smtp_to     = await get_setting(db, "smtp_to", "")

    tasks = []
    text = f"{title}\n{message}"
    color = 0xe74c3c if severity == "critical" else 0x2ecc71  # red or green

    if tg_token and tg_chat:
        tasks.append(_send_telegram(tg_token, tg_chat, f"<b>{title}</b>\n{message}"))
    if dc_webhook:
        tasks.append(_send_discord(dc_webhook, title, message, color))
    if smtp_host and smtp_user and smtp_pw_enc and smtp_to:
        try:
            smtp_pw = decrypt_value(smtp_pw_enc)
        except Exception:
            smtp_pw = smtp_pw_enc
        tasks.append(_send_email(
            smtp_host, int(smtp_port), smtp_user, smtp_pw,
            smtp_from or smtp_user, smtp_to, title, message
        ))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning("Notification channel %d failed: %s", i, r)
