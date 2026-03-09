"""Tests for the notification service."""
from unittest.mock import AsyncMock, patch

import notifications


def _clear_rate_limit():
    """Clear the rate limit cache between tests."""
    notifications._recent.clear()


async def test_notify_disabled_skips_all():
    """When notify_enabled is '0', no channels should be called."""
    _clear_rate_limit()
    async def fake_get_setting(db, key, default=""):
        return {"notify_enabled": "0"}.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("Test", "Message")
        mock_tg.assert_not_called()


async def test_notify_sends_telegram():
    """When telegram is configured, it should be called."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC",
        "telegram_chat_id": "456",
        "discord_webhook_url": "",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("Host offline", "Server1 is down", severity="critical")
        mock_tg.assert_called_once()
        mock_dc.assert_not_called()


async def test_notify_sends_discord():
    """When discord is configured, it should be called."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "", "telegram_chat_id": "",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("Test discord", "Message")
        mock_tg.assert_not_called()
        mock_dc.assert_called_once()


async def test_notify_channel_failure_doesnt_crash():
    """If one channel fails, the others should still work."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    async def failing_telegram(*args, **kwargs):
        raise ConnectionError("Network error")

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", side_effect=failing_telegram), \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("Test failure", "Message")
        mock_dc.assert_called_once()


async def test_notify_severity_color():
    """Critical should use red, info should use green for Discord."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "", "telegram_chat_id": "",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifications.notify("Critical alert", "Down", severity="critical")
        color_critical = mock_dc.call_args[0][3]

        _clear_rate_limit()
        mock_dc.reset_mock()
        await notifications.notify("Info alert", "Up", severity="info")
        color_info = mock_dc.call_args[0][3]

        assert color_critical == 0xe74c3c  # red
        assert color_info == 0x2ecc71  # green


async def test_rate_limiting():
    """Same title should be rate-limited within cooldown window."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "discord_webhook_url": "",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifications.notify("Duplicate alert", "First")
        await notifications.notify("Duplicate alert", "Second")  # Should be rate-limited
        assert mock_tg.call_count == 1  # Only first call went through
