"""PC-local DPAPI storage compatible with the existing login and notifier readers."""

import json
import os
import re
import uuid
from urllib.parse import urlsplit


def encrypt(path, plain, label):
    import win32crypt

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_bytes(
        win32crypt.CryptProtectData(plain, label, None, None, None, 0)
    )
    os.replace(temporary, path)


def save_login(context, username, password):
    if any(
        not isinstance(v, str) or not 1 <= len(v) <= 256 for v in (username, password)
    ):
        raise ValueError("Enter both account fields (maximum 256 characters each)")
    encrypt(
        context.credentials,
        json.dumps({"username": username, "password": password}).encode(),
        "Conquest account",
    )


def save_notification_webhook(context, value):
    url = urlsplit(value.strip())
    if (
        url.scheme != "https"
        or url.netloc not in ("discord.com", "discordapp.com")
        or not re.fullmatch(r"/api/(?:v\d+/)?webhooks/\d+/[A-Za-z0-9_-]+", url.path)
    ):
        raise ValueError("Enter a valid Discord webhook URL")
    path = (
        context.root / "machine-state/.runtime/merchants/shops-webhook.dpapi"
        if context.profile.role == "Merchant"
        else context.state_dir / ".runtime/discord-webhook.dpapi"
    )
    encrypt(path, value.strip().encode(), "Conquest Discord")
