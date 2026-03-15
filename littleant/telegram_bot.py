"""
LittleAnt V14 - Telegram Bot
User communicates with AI butler via Telegram.
Pure stdlib, no third-party dependencies
"""
from __future__ import annotations
import json
import time
import logging
import threading
import urllib.request
import urllib.error
import urllib.parse
import traceback
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram Bot (Long Polling, zero dependencies)"""

    def __init__(self, token: str):
        self.token = token
        self.api_base = f"https://api.telegram.org/bot{token}"
        self.offset = 0
        self.running = False
        self.handlers: dict[str, Callable] = {}
        self.message_handler: Optional[Callable] = None
        self.callback_handler: Optional[Callable] = None
        self._menu_commands: list[dict] = []

    def set_menu_commands(self, commands: list[tuple[str, str]]):
        """Set bot menu commands. Each item is (command, description)."""
        self._menu_commands = [{"command": c, "description": d} for c, d in commands]

    # ============================================================
    # API calls
    # ============================================================

    def _call(self, method: str, data: dict = None) -> dict:
        """Call Telegram Bot API"""
        url = f"{self.api_base}/{method}"
        if data:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            if not result.get("ok"):
                logger.error(f"Telegram API error: {result}")
            return result
        except urllib.error.URLError as e:
            logger.error(f"Telegram API request failed: {e}")
            return {"ok": False, "error": str(e)}

    def send_message(self, chat_id: int, text: str,
                     reply_markup: dict = None,
                     parse_mode: str = None) -> dict:
        """Sending message"""
        # Telegram message length limit 4096
        if len(text) > 4000:
            # Send in chunks
            parts = self._split_text(text, 4000)
            result = None
            for part in parts:
                data = {"chat_id": chat_id, "text": part}
                if parse_mode:
                    data["parse_mode"] = parse_mode
                result = self._call("sendMessage", data)
                time.sleep(0.3)
            # Add buttons to last chunk
            if reply_markup and result:
                pass  # Buttons already on last message
            return result
        else:
            data = {"chat_id": chat_id, "text": text}
            if reply_markup:
                data["reply_markup"] = reply_markup
            if parse_mode:
                data["parse_mode"] = parse_mode
            return self._call("sendMessage", data)

    def edit_message(self, chat_id: int, message_id: int, text: str,
                     reply_markup: dict = None) -> dict:
        """Edit a sent message"""
        data = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self._call("editMessageText", data)

    def answer_callback(self, callback_query_id: str, text: str = None):
        """Answer inline keyboard click"""
        data = {"callback_query_id": callback_query_id}
        if text:
            data["text"] = text
        return self._call("answerCallbackQuery", data)

    def send_typing(self, chat_id: int):
        """Send typing indicator"""
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    def download_file(self, file_id: str) -> str | None:
        """Download a file from Telegram, return local path."""
        try:
            result = self._call("getFile", {"file_id": file_id})
            if not result.get("ok"):
                return None
            file_path = result["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"

            import urllib.request, os, tempfile
            ext = os.path.splitext(file_path)[1] or ".bin"
            local_dir = os.path.join(tempfile.gettempdir(), "littleant_files")
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, f"{file_id[:16]}{ext}")

            urllib.request.urlretrieve(url, local_path)
            logger.info(f"Downloaded file: {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"File download failed: {e}")
            return None

    # ============================================================
    # Handler registration
    # ============================================================

    def on_command(self, command: str):
        """Register command handler decorator"""
        def decorator(func):
            self.handlers[command] = func
            return func
        return decorator

    def on_message(self, func):
        """Register message handler"""
        self.message_handler = func
        return func

    def on_callback(self, func):
        """Register callback query handler"""
        self.callback_handler = func
        return func

    # ============================================================
    # Polling main loop
    # ============================================================

    def start_polling(self):
        """Start long polling"""
        self.running = True
        logger.info("Telegram Bot Starting polling...")

        # Validate token
        me = self._call("getMe")
        if me.get("ok"):
            bot_info = me["result"]
            logger.info(f"Bot connected: @{bot_info.get('username')} ({bot_info.get('first_name')})")
        else:
            logger.error(f"Invalid bot token: {me}")
            return

        # Set bot menu commands (clears any old ones)
        if self._menu_commands:
            self._call("setMyCommands", {"commands": self._menu_commands})

        while self.running:
            try:
                updates = self._call("getUpdates", {
                    "offset": self.offset,
                    "timeout": 30,
                })
                if not updates.get("ok"):
                    time.sleep(5)
                    continue

                for update in updates.get("result", []):
                    self.offset = update["update_id"] + 1
                    self._process_update(update)

            except KeyboardInterrupt:
                logger.info("Interrupt signal received, stopping")
                self.running = False
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)

    def stop(self):
        self.running = False

    def _process_update(self, update: dict):
        """Process single update"""
        try:
            # Callback query (inline keyboard click)
            if "callback_query" in update:
                cb = update["callback_query"]
                if self.callback_handler:
                    self.callback_handler(cb)
                return

            msg = update.get("message")
            if not msg:
                return

            chat_id = msg["chat"]["id"]

            # Extract reply/quote context
            reply_context = ""
            if "reply_to_message" in msg:
                replied = msg["reply_to_message"]
                reply_text = replied.get("text", "")
                if reply_text:
                    reply_context = reply_text

            # Handle photo messages
            if "photo" in msg and not msg.get("text"):
                caption = msg.get("caption", "")
                # Get highest resolution photo
                photo = msg["photo"][-1]
                file_id = photo["file_id"]
                file_path = self.download_file(file_id)
                if self.message_handler:
                    msg["_type"] = "photo"
                    msg["_file_path"] = file_path
                    msg["_caption"] = caption
                    msg["_reply_context"] = reply_context
                    msg["text"] = caption or "[photo]"
                    self.message_handler(msg)
                return

            # Handle document/file messages
            if "document" in msg and not msg.get("text"):
                caption = msg.get("caption", "")
                doc = msg["document"]
                file_id = doc["file_id"]
                file_name = doc.get("file_name", "unknown")
                file_path = self.download_file(file_id)
                if self.message_handler:
                    msg["_type"] = "document"
                    msg["_file_path"] = file_path
                    msg["_file_name"] = file_name
                    msg["_caption"] = caption
                    msg["_reply_context"] = reply_context
                    msg["text"] = caption or f"[file: {file_name}]"
                    self.message_handler(msg)
                return

            # Text messages
            if "text" not in msg:
                return

            text = msg["text"]
            msg["_type"] = "text"
            msg["_reply_context"] = reply_context

            # Command handling
            if text.startswith("/"):
                cmd = text.split()[0].split("@")[0][1:]
                handler = self.handlers.get(cmd)
                if handler:
                    handler(msg)
                else:
                    self.send_message(chat_id, f"Unknown command: /{cmd}. Type /help for help")
                return

            # Regular message
            if self.message_handler:
                self.message_handler(msg)

        except Exception as e:
            logger.error(f"Handle message error: {e}\n{traceback.format_exc()}")
            chat_id = update.get("message", {}).get("chat", {}).get("id")
            if chat_id:
                self.send_message(chat_id, f"⚠️ Processing error: {str(e)[:200]}")

    def _split_text(self, text: str, max_len: int) -> list[str]:
        """Split long text by lines"""
        parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                if current:
                    parts.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            parts.append(current)
        return parts or [text[:max_len]]
