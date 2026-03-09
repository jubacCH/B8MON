"""Notification senders for Nodeglow – Telegram, Discord, Webhook, Email.

Features:
  - Multi-channel delivery (fire-and-forget, non-blocking)
  - Rate limiting / cooldown to prevent alert fatigue
  - Notification history persisted to DB
  - HTML email with styled template
"""
import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)

# ── Rate Limiting ─────────────────────────────────────────────────────────────
# In-memory cooldown: same title won't fire again within cooldown window
_recent: dict[str, float] = {}
_COOLDOWN_SECONDS = 300  # 5 minutes


def _is_rate_limited(title: str) -> bool:
    """Check if this notification was sent recently (prevents alert storms)."""
    now = time.monotonic()
    # Cleanup old entries
    stale = [k for k, v in _recent.items() if now - v > _COOLDOWN_SECONDS * 2]
    for k in stale:
        del _recent[k]
    key = title.strip().lower()
    if key in _recent and now - _recent[key] < _COOLDOWN_SECONDS:
        return True
    _recent[key] = now
    return False


# ── History Logging ───────────────────────────────────────────────────────────

async def _log_notification(channel: str, title: str, message: str,
                            severity: str, status: str = "sent",
                            error: str | None = None) -> None:
    """Persist notification to DB for audit trail."""
    try:
        from database import AsyncSessionLocal
        from models.notification import NotificationLog
        async with AsyncSessionLocal() as db:
            db.add(NotificationLog(
                channel=channel, title=title, message=message,
                severity=severity, status=status, error=error,
            ))
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to log notification: %s", exc)


# ── Channel Senders ───────────────────────────────────────────────────────────

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


async def _send_webhook(url: str, secret: str, title: str, message: str,
                        severity: str = "critical") -> None:
    payload = {"title": title, "message": message, "severity": severity,
               "timestamp": int(time.time()), "source": "nodeglow"}
    body = json.dumps(payload, separators=(",", ":"))
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Nodeglow-Signature"] = f"sha256={sig}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, content=body, headers=headers)
        resp.raise_for_status()


def _build_html_email(title: str, message: str, severity: str) -> str:
    """Build a styled HTML email body."""
    colors = {
        "critical": ("#FB7185", "#1a0a0e"),
        "warning":  ("#FBBF24", "#1a1408"),
        "info":     ("#38BDF8", "#0a1628"),
    }
    accent, bg_tint = colors.get(severity, colors["info"])
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:0 auto;padding:0">
  <div style="background:#0B1120;border-radius:12px;overflow:hidden;border:1px solid #1E293B">
    <div style="padding:24px 28px;border-bottom:1px solid #1E293B;background:{bg_tint}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{accent}"></span>
        <span style="color:{accent};font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:2px">{severity}</span>
      </div>
      <h1 style="color:#E2E8F0;font-size:18px;font-weight:600;margin:0;line-height:1.4">{title}</h1>
    </div>
    <div style="padding:20px 28px">
      <p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0;white-space:pre-line">{message}</p>
    </div>
    <div style="padding:16px 28px;border-top:1px solid #1E293B;text-align:center">
      <span style="color:#475569;font-size:11px;letter-spacing:3px;font-weight:500">NODEGLOW</span>
    </div>
  </div>
</div>"""


async def _send_email(host: str, port: int, user: str, password: str,
                      from_addr: str, to_addr: str, subject: str,
                      body_text: str, body_html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    loop = asyncio.get_event_loop()

    def _send():
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx) as s:
                s.login(user, password)
                s.sendmail(from_addr, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host, port) as s:
                s.starttls(context=ctx)
                s.login(user, password)
                s.sendmail(from_addr, [to_addr], msg.as_string())
    await loop.run_in_executor(None, _send)


# ── Public API ────────────────────────────────────────────────────────────────

async def notify(title: str, message: str, severity: str = "critical") -> None:
    """Send notification to all configured channels. Fire-and-forget safe."""
    # Rate limit check
    if _is_rate_limited(title):
        logger.debug("Notification rate-limited: %s", title)
        return

    from database import AsyncSessionLocal, decrypt_value, get_setting
    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "notify_enabled", "0")
        if enabled != "1":
            return

        tg_token    = await get_setting(db, "telegram_bot_token", "")
        tg_chat     = await get_setting(db, "telegram_chat_id", "")
        dc_webhook  = await get_setting(db, "discord_webhook_url", "")
        wh_url      = await get_setting(db, "webhook_url", "")
        wh_secret   = await get_setting(db, "webhook_secret", "")
        smtp_host   = await get_setting(db, "smtp_host", "")
        smtp_port   = await get_setting(db, "smtp_port", "587")
        smtp_user   = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_from   = await get_setting(db, "smtp_from", "")
        smtp_to     = await get_setting(db, "smtp_to", "")

    channels = []  # (name, coroutine) pairs
    color = {"critical": 0xe74c3c, "warning": 0xf39c12, "info": 0x2ecc71}.get(severity, 0x2ecc71)

    if tg_token and tg_chat:
        channels.append(("telegram", _send_telegram(tg_token, tg_chat, f"<b>{title}</b>\n{message}")))
    if dc_webhook:
        channels.append(("discord", _send_discord(dc_webhook, title, message, color)))
    if wh_url:
        channels.append(("webhook", _send_webhook(wh_url, wh_secret, title, message, severity)))
    if smtp_host and smtp_user and smtp_pw_enc and smtp_to:
        try:
            smtp_pw = decrypt_value(smtp_pw_enc)
        except Exception:
            smtp_pw = smtp_pw_enc
        html_body = _build_html_email(title, message, severity)
        channels.append(("email", _send_email(
            smtp_host, int(smtp_port), smtp_user, smtp_pw,
            smtp_from or smtp_user, smtp_to,
            f"[Nodeglow] {title}", f"{title}\n{message}", html_body,
        )))

    if not channels:
        return

    results = await asyncio.gather(*[coro for _, coro in channels], return_exceptions=True)

    # Log results to DB
    for (ch_name, _), result in zip(channels, results):
        if isinstance(result, Exception):
            logger.warning("Notification %s failed: %s", ch_name, result)
            await _log_notification(ch_name, title, message, severity, "failed", str(result))
        else:
            await _log_notification(ch_name, title, message, severity, "sent")
