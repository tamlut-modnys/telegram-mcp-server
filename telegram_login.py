#!/usr/bin/env python3
"""Persistent Telegram login helper for the local MCP server."""

import asyncio
import json
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

CONFIG_DIR = Path.home() / ".telegram-mcp"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSION_BASE_PATH = Path(
    os.environ.get("TELEGRAM_SESSION_PATH", str(CONFIG_DIR / "session"))
)
if SESSION_BASE_PATH.suffix == ".session":
    SESSION_BASE_PATH = SESSION_BASE_PATH.with_suffix("")
STRING_SESSION_FILE = Path(
    os.environ.get("TELEGRAM_STRING_SESSION_FILE", str(CONFIG_DIR / "session.string"))
)


def _load_config() -> dict:
    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as file:
            config = json.load(file)

    return {
        "api_id": config.get("api_id") or os.environ.get("TELEGRAM_API_ID", ""),
        "api_hash": config.get("api_hash") or os.environ.get("TELEGRAM_API_HASH", ""),
        "phone": config.get("phone") or os.environ.get("TELEGRAM_PHONE", ""),
    }


def _write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)


async def main() -> None:
    cfg = _load_config()
    if not cfg["api_id"] or not cfg["api_hash"] or not cfg["phone"]:
        print("ERROR: Missing Telegram credentials in ~/.telegram-mcp/config.json")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_BASE_PATH), int(cfg["api_id"]), cfg["api_hash"])
    await client.start(phone=cfg["phone"])

    me = await client.get_me()
    session_string = StringSession.save(client.session).strip()
    if session_string:
        _write_secret_file(STRING_SESSION_FILE, session_string)
    print(f"Login successful: {me.first_name} {me.last_name or ''}".strip())
    print(f"Session saved to: {SESSION_BASE_PATH}.session")
    if session_string:
        print(f"String session saved to: {STRING_SESSION_FILE}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
