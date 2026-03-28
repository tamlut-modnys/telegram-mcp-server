# Telegram MCP Server for Claude Cowork

This project exposes a Telegram account to Claude Cowork through MCP. It uses
[`Telethon`](https://docs.telethon.dev/) to connect to Telegram and
[`FastMCP`](https://modelcontextprotocol.io/) to expose tools that Claude can
call over `stdio`, which is the most reliable way to connect local MCP servers
to Claude Desktop and Cowork.

## Features

- List recent chats and unread counts
- Look up chat metadata
- Search chats by name
- Read recent messages
- Send a message or reply
- Search messages globally or inside a chat
- List contacts
- Mark chats as read

## Files

- `telegram_mcp_server.py`: the MCP server
- `telegram_login.py`: one-time login helper
- `requirements.txt`: Python dependencies

## Prerequisites

- Python 3.11+ or 3.12+
- A Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org)
- Claude Desktop / Claude Cowork with local MCP enabled

## Install

```bash
cd telegram-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure Telegram credentials

Create `~/.telegram-mcp/config.json`:

```json
{
  "api_id": "123456",
  "api_hash": "your_api_hash",
  "phone": "+15551234567"
}
```

You can also provide the same values through environment variables:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_PHONE`

## One-time login

Run:

```bash
~/.telegram-mcp/venv/bin/python3 telegram_login.py
```

If you are using the repo-local virtualenv instead:

```bash
.venv/bin/python3 telegram_login.py
```

Telegram will prompt for your login code and any 2FA password. On success it
stores the session at `~/.telegram-mcp/session.session`.

## Connect it to Claude Cowork

Add this entry to:

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "telegram": {
      "command": "/ABSOLUTE/PATH/TO/.venv/bin/python3",
      "args": [
        "/ABSOLUTE/PATH/TO/telegram_mcp_server.py"
      ]
    }
  }
}
```

If you are using the shared environment from `~/.telegram-mcp/venv`, the entry
would look like:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "/Users/you/.telegram-mcp/venv/bin/python3",
      "args": [
        "/path/to/telegram-mcp-server/telegram_mcp_server.py"
      ]
    }
  }
}
```

Then fully restart Claude Desktop. Cowork should discover the Telegram tools on
startup.

## Available tools

- `list_chats(limit=30)`
- `get_chat_info(chat_id)`
- `search_chats(query, limit=10)`
- `read_messages(chat_id, limit=20)`
- `send_message(chat_id, text, reply_to_message_id=0)`
- `search_messages(query, chat_id=0, limit=20, sender_name="")`
- `get_contacts(limit=50)`
- `mark_as_read(chat_id)`

## Optional SSE debugging mode

For local debugging only, you can run the server over SSE:

```bash
TELEGRAM_MCP_TRANSPORT=sse \
TELEGRAM_MCP_HOST=127.0.0.1 \
TELEGRAM_MCP_PORT=8457 \
.venv/bin/python3 telegram_mcp_server.py
```

This is useful for testing transport behavior, but Claude Cowork should use the
`stdio` setup above.

## Troubleshooting

If Claude does not show the tools:

- Verify the paths in `claude_desktop_config.json` are absolute
- Restart Claude Desktop completely
- Re-run `telegram_login.py` if the Telegram session expired
- Confirm your config file or environment variables contain `api_id` and
  `api_hash`

If authentication fails:

- Make sure your phone number includes the country code
- Make sure you are using credentials from `my.telegram.org`, not a bot token

## Security notes

Do not commit:

- `~/.telegram-mcp/config.json`
- `~/.telegram-mcp/session.session`
- any exported logs containing message content
