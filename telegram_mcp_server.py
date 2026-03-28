#!/usr/bin/env python3
"""
Telegram MCP server for Claude Cowork and Claude Desktop.

The server defaults to stdio for Claude's local MCP flow. For debugging, it can
also run over SSE by setting TELEGRAM_MCP_TRANSPORT=sse.
"""

import json
import mimetypes
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from telethon import TelegramClient
from telethon.sessions import SQLiteSession, StringSession
from telethon.tl.types import (
    Channel,
    Chat,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
    User,
)

CONFIG_DIR = Path.home() / ".telegram-mcp"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSION_BASE_PATH = Path(
    os.environ.get("TELEGRAM_SESSION_PATH", str(CONFIG_DIR / "session"))
)
if SESSION_BASE_PATH.suffix == ".session":
    SESSION_BASE_PATH = SESSION_BASE_PATH.with_suffix("")
SESSION_FILE = SESSION_BASE_PATH.with_suffix(".session")
STRING_SESSION_FILE = Path(
    os.environ.get("TELEGRAM_STRING_SESSION_FILE", str(CONFIG_DIR / "session.string"))
)
MEDIA_DIR = Path(
    os.environ.get("TELEGRAM_MEDIA_DIR", str(CONFIG_DIR / "downloads"))
)
MAX_DOWNLOAD_BYTES = int(
    os.environ.get("TELEGRAM_MAX_DOWNLOAD_BYTES", str(25 * 1024 * 1024))
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
        "string_session": config.get("string_session")
        or os.environ.get("TELEGRAM_STRING_SESSION", ""),
    }


def _write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)


def _load_string_session(cfg: dict) -> str:
    value = str(cfg.get("string_session") or "").strip()
    if value:
        return value

    if STRING_SESSION_FILE.exists():
        return STRING_SESSION_FILE.read_text(encoding="utf-8").strip()

    return ""


def _migrate_sqlite_session() -> str:
    if not SESSION_FILE.exists():
        return ""

    session = SQLiteSession(str(SESSION_BASE_PATH))
    try:
        value = StringSession.save(session).strip()
    finally:
        session.close()

    if value:
        _write_secret_file(STRING_SESSION_FILE, value)

    return value


_client: TelegramClient | None = None


async def _get_client() -> TelegramClient:
    global _client
    if _client is not None and _client.is_connected():
        return _client

    cfg = _load_config()
    if not cfg["api_id"] or not cfg["api_hash"]:
        raise RuntimeError(
            "Missing Telegram credentials. Ensure ~/.telegram-mcp/config.json exists."
        )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    session_string = _load_string_session(cfg)
    if not session_string:
        session_string = _migrate_sqlite_session()

    session = StringSession(session_string) if session_string else str(SESSION_BASE_PATH)
    client = TelegramClient(session, int(cfg["api_id"]), cfg["api_hash"])
    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError(
            "Not logged in. Run ~/.telegram-mcp/telegram_login.py first."
        )

    if session_string and not STRING_SESSION_FILE.exists():
        _write_secret_file(STRING_SESSION_FILE, session_string)

    _client = client
    return client


def _entity_name(entity) -> str:
    if isinstance(entity, User):
        parts = [entity.first_name or "", entity.last_name or ""]
        return " ".join(part for part in parts if part) or entity.username or str(
            entity.id
        )
    if isinstance(entity, (Chat, Channel)):
        return entity.title or str(entity.id)
    return str(getattr(entity, "id", "unknown"))


def _document_file_name(message) -> str:
    document = getattr(message.media, "document", None)
    if not document:
        return ""

    for attribute in document.attributes:
        name = getattr(attribute, "file_name", None)
        if name:
            return str(name)

    return ""


def _media_kind(message) -> str:
    if message.media is None:
        return ""
    if isinstance(message.media, MessageMediaPhoto):
        return "photo"
    if isinstance(message.media, MessageMediaDocument):
        document = message.media.document
        if document:
            mime_type = str(document.mime_type or "").lower()
            if "video" in mime_type:
                return "video"
            if "audio" in mime_type:
                return "audio"
            if mime_type == "image/gif":
                return "gif"
            if mime_type.startswith("image/"):
                return "image"
        return "document"
    if isinstance(message.media, MessageMediaGeo):
        return "location"
    if isinstance(message.media, MessageMediaContact):
        return "contact"
    if isinstance(message.media, MessageMediaPoll):
        return "poll"
    if isinstance(message.media, MessageMediaWebPage):
        return ""
    return "media"


def _media_info(message) -> dict | None:
    kind = _media_kind(message)
    if not kind:
        return None

    info: dict[str, object] = {
        "type": kind,
        "downloadable": kind in {"photo", "image", "gif", "video", "audio", "document"},
    }

    file_info = getattr(message, "file", None)
    file_name = _document_file_name(message)
    mime_type = str(getattr(file_info, "mime_type", "") or "")
    size = getattr(file_info, "size", None)

    if file_name:
        info["file_name"] = file_name
    if mime_type:
        info["mime_type"] = mime_type
    if size is not None:
        info["size_bytes"] = int(size)

    width = getattr(file_info, "width", None)
    height = getattr(file_info, "height", None)
    duration = getattr(file_info, "duration", None)
    if width is not None:
        info["width"] = int(width)
    if height is not None:
        info["height"] = int(height)
    if duration is not None:
        info["duration_seconds"] = int(duration)

    if kind == "location":
        geo = getattr(message.media, "geo", None)
        if geo:
            info["latitude"] = geo.lat
            info["longitude"] = geo.long
    elif kind == "contact":
        info["first_name"] = message.media.first_name or ""
        info["last_name"] = message.media.last_name or ""
        if getattr(message.media, "phone_number", None):
            info["phone_number"] = message.media.phone_number
    elif kind == "poll":
        question = message.media.poll.question
        info["question"] = (
            question.text if hasattr(question, "text") else str(question)
        )

    return info


def _media_text(message) -> str:
    info = _media_info(message)
    if info is None:
        return ""

    kind = str(info["type"])
    if kind == "photo":
        return "[Photo]"
    if kind == "video":
        return "[Video]"
    if kind == "audio":
        return "[Audio]"
    if kind == "gif":
        return "[GIF]"
    if kind in {"document", "image"}:
        file_name = str(info.get("file_name") or "").strip()
        if file_name:
            return f"[Document: {file_name}]"
        return "[Document]"
    if kind == "location":
        return "[Location]"
    if kind == "contact":
        return (
            f"[Contact: {message.media.first_name} {message.media.last_name}]"
        ).strip()
    if kind == "poll":
        return f"[Poll: {info.get('question', '')}]".rstrip()
    return "[Media]"


def _safe_file_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "attachment"


def _guess_extension(media_info: dict | None) -> str:
    if not media_info:
        return ""

    file_name = str(media_info.get("file_name") or "")
    if file_name:
        return Path(file_name).suffix

    mime_type = str(media_info.get("mime_type") or "")
    guessed = mimetypes.guess_extension(mime_type)
    if guessed:
        return guessed

    kind = str(media_info.get("type") or "")
    if kind == "photo":
        return ".jpg"
    if kind == "gif":
        return ".gif"
    return ""


def _download_target_path(message, media_info: dict | None) -> Path:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    chat_dir = MEDIA_DIR / f"chat_{message.chat_id}"
    chat_dir.mkdir(parents=True, exist_ok=True)

    file_name = str((media_info or {}).get("file_name") or "").strip()
    extension = _guess_extension(media_info)
    if file_name:
        base_name = _safe_file_name(Path(file_name).name)
        return chat_dir / f"message_{message.id}_{base_name}"

    return chat_dir / f"message_{message.id}{extension}"


def _message_to_dict(message, chat_title: str = "") -> dict:
    sender_name = "unknown"
    if message.sender:
        sender_name = _entity_name(message.sender)

    text = message.text or ""
    media = _media_text(message)
    media_info = _media_info(message)
    if media and text:
        text = f"{media} {text}"
    elif media:
        text = media

    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "chat_title": chat_title,
        "date": message.date.isoformat() if message.date else "",
        "sender": sender_name,
        "text": text,
        "caption": message.text or "",
        "has_media": media_info is not None,
        "media": media_info,
        "reply_to_msg_id": (
            message.reply_to.reply_to_msg_id if message.reply_to else None
        ),
    }


mcp = FastMCP("telegram")


@mcp.tool()
async def list_chats(limit: int = 30) -> str:
    """List your most recent Telegram chats."""
    limit = min(limit, 100)
    client = await _get_client()
    dialogs = await client.get_dialogs(limit=limit)
    results = []
    for dialog in dialogs:
        results.append(
            {
                "id": dialog.id,
                "title": dialog.title or dialog.name or str(dialog.id),
                "type": type(dialog.entity).__name__,
                "unread_count": dialog.unread_count,
                "last_message_preview": (
                    (dialog.message.text or _media_text(dialog.message))[:120]
                    if dialog.message
                    else ""
                ),
            }
        )
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_chat_info(chat_id: int) -> str:
    """Get detailed information about a specific chat."""
    client = await _get_client()
    entity = await client.get_entity(chat_id)
    info = {
        "id": entity.id,
        "title": _entity_name(entity),
        "type": type(entity).__name__,
    }
    if isinstance(entity, User):
        info["username"] = entity.username
        info["phone"] = entity.phone
        info["bot"] = entity.bot
    elif isinstance(entity, (Chat, Channel)) and hasattr(
        entity, "participants_count"
    ):
        info["member_count"] = entity.participants_count
    return json.dumps(info, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_chats(query: str, limit: int = 10) -> str:
    """Search for chats by name or username."""
    client = await _get_client()
    dialogs = await client.get_dialogs(limit=100)
    query_lower = query.lower()
    results = []
    for dialog in dialogs:
        name = dialog.title or dialog.name or ""
        if query_lower in name.lower():
            results.append(
                {
                    "id": dialog.id,
                    "title": name,
                    "type": type(dialog.entity).__name__,
                }
            )
            if len(results) >= limit:
                break
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def download_media(chat_id: int, message_id: int) -> str:
    """Download a message attachment to a local file and return its path."""
    client = await _get_client()
    entity = await client.get_entity(chat_id)
    message = await client.get_messages(entity, ids=message_id)

    if isinstance(message, list):
        message = message[0] if message else None

    if message is None:
        raise RuntimeError(
            f"Message {message_id} was not found in chat {chat_id}."
        )

    media_info = _media_info(message)
    if media_info is None or not media_info.get("downloadable"):
        raise RuntimeError(
            f"Message {message_id} does not contain downloadable media."
        )

    size_bytes = media_info.get("size_bytes")
    if isinstance(size_bytes, int) and size_bytes > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(
            f"Message {message_id} media is {size_bytes} bytes, above the "
            f"configured limit of {MAX_DOWNLOAD_BYTES} bytes."
        )

    target_path = _download_target_path(message, media_info)
    downloaded = await client.download_media(message, file=str(target_path))
    if not downloaded:
        raise RuntimeError(f"Failed to download media for message {message_id}.")

    downloaded_path = Path(downloaded).resolve()
    return json.dumps(
        {
            "status": "downloaded",
            "chat_id": chat_id,
            "message_id": message_id,
            "path": str(downloaded_path),
            "caption": message.text or "",
            "media": media_info,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def read_messages(chat_id: int, limit: int = 20) -> str:
    """Read recent messages from a chat."""
    limit = min(limit, 100)
    client = await _get_client()
    entity = await client.get_entity(chat_id)
    title = _entity_name(entity)
    messages = await client.get_messages(entity, limit=limit)
    return json.dumps(
        [_message_to_dict(message, title) for message in messages],
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def send_message(
    chat_id: int, text: str, reply_to_message_id: int = 0
) -> str:
    """Send a text message to a chat."""
    client = await _get_client()
    entity = await client.get_entity(chat_id)
    message = await client.send_message(
        entity,
        text,
        reply_to=reply_to_message_id if reply_to_message_id else None,
    )
    return json.dumps(
        {"status": "sent", "message_id": message.id, "chat_id": chat_id},
        indent=2,
    )


@mcp.tool()
async def search_messages(
    query: str,
    chat_id: int = 0,
    limit: int = 20,
    sender_name: str = "",
) -> str:
    """Search for messages across all chats or within a specific chat."""
    limit = min(limit, 100)
    client = await _get_client()
    entity = await client.get_entity(chat_id) if chat_id else None

    messages = []
    async for message in client.iter_messages(entity, search=query, limit=limit):
        if sender_name:
            message_sender = _entity_name(message.sender) if message.sender else ""
            if sender_name.lower() not in message_sender.lower():
                continue
        chat_title = _entity_name(message.chat) if message.chat else ""
        messages.append(_message_to_dict(message, chat_title))
    return json.dumps(messages, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_contacts(limit: int = 50) -> str:
    """Get your Telegram contacts list."""
    client = await _get_client()
    from telethon.tl.functions.contacts import GetContactsRequest

    result = await client(GetContactsRequest(hash=0))
    contacts = []
    for user in result.users[:limit]:
        contacts.append(
            {
                "id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
                "phone": user.phone or "",
            }
        )
    return json.dumps(contacts, ensure_ascii=False, indent=2)


@mcp.tool()
async def mark_as_read(chat_id: int) -> str:
    """Mark all messages in a chat as read."""
    client = await _get_client()
    entity = await client.get_entity(chat_id)
    await client.send_read_acknowledge(entity)
    return json.dumps({"status": "marked_as_read", "chat_id": chat_id})


def _run() -> None:
    transport = os.environ.get("TELEGRAM_MCP_TRANSPORT", "stdio").strip().lower()

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport in {"sse", "streamable-http"}:
        mcp.settings.host = os.environ.get("TELEGRAM_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("TELEGRAM_MCP_PORT", "8457"))
        mcp.run(transport=transport)
        return

    raise SystemExit(f"Unsupported TELEGRAM_MCP_TRANSPORT: {transport}")


if __name__ == "__main__":
    _run()
