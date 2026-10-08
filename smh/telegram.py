"""Minimal Telegram Bot API client (stdlib only): HTML send with plain-text fallback, forum topics, one button row,
long-poll loop. 429 retry_after is honoured; 5xx/timeouts are not resent (the message may have gone through)."""
from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class TelegramError(Exception):
    """Telegram answered ok=false: the request was refused, nothing was sent."""


class Ambiguous(Exception):
    """The request may or may not have been delivered (timeout, 5xx). The caller must not resend."""


class Telegram:
    def __init__(self, token: str, chat_id: str = "", thread_id: int | None = None, sleep=time.sleep):
        self.base, self.chat_id, self.thread_id, self._sleep = f"https://api.telegram.org/bot{token}/", chat_id, thread_id, sleep
        self.last = 0.0

    def call(self, method: str, **params):
        for _ in range(6):
            if method != "getUpdates":   # ~1 msg/s keeps us under the group limit
                wait = self.last + 1.1 - time.time()
                if wait > 0:
                    self._sleep(wait)
                self.last = time.time()
            req = urllib.request.Request(self.base + method, data=json.dumps(params).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=70) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as ex:
                try:
                    data = json.load(ex)
                except ValueError:
                    data = {}
                if ex.code == 429:
                    retry = float(data.get("parameters", {}).get("retry_after", 5))
                    log.warning("429 on %s, waiting %.0f s", method, retry)
                    self._sleep(retry + 1)
                    continue
                if ex.code >= 500:
                    raise Ambiguous(f"{method}: HTTP {ex.code}")
                raise TelegramError(f"{method}: {data.get('description', ex.code)}")
            except (urllib.error.URLError, TimeoutError, OSError) as ex:
                raise Ambiguous(f"{method}: {type(ex).__name__}")
            if not data.get("ok"):
                raise TelegramError(f"{method}: {data.get('description')}")
            return data["result"]
        raise Ambiguous(f"{method}: throttled too often")

    def send(self, chat_id, text: str, thread_id: int | None = None, buttons: list[tuple[str, str]] | None = None) -> int:
        """HTML message; on 'can't parse entities' it is resent as plain text so it is never lost."""
        extra = {"message_thread_id": thread_id} if thread_id else {}
        if buttons:
            extra["reply_markup"] = {"inline_keyboard": [[{"text": a, "callback_data": b} for a, b in buttons]]}
        try:
            r = self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                          link_preview_options={"is_disabled": True}, **extra)
        except TelegramError as ex:
            if "parse entities" not in str(ex).lower():
                raise
            r = self.call("sendMessage", chat_id=chat_id, text=html.unescape(re.sub(r"<[^>]+>", "", text)),
                          link_preview_options={"is_disabled": True}, **extra)
        return r["message_id"]

    def post(self, texts: list[str], buttons: list[tuple[str, str]] | None = None,
             chat_id=None, thread_id: int | None = None) -> list[int]:
        """Send a multi-message report (default: the bot's own chat and topic); buttons on the last message only."""
        chat = chat_id or self.chat_id
        thread = thread_id if chat_id else self.thread_id
        return [self.send(chat, t, thread_id=thread, buttons=buttons if i == len(texts) - 1 else None)
                for i, t in enumerate(texts)]

    def poll(self, store, handler, stop=None) -> None:
        """Long poll getUpdates forever; the offset lives in the store so a restart never replays a command."""
        offset = int(store.get("tg_offset", "0"))
        while not (stop and stop.is_set()):
            try:
                updates = self.call("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
            except (Ambiguous, TelegramError) as ex:
                log.warning("getUpdates: %s", ex)
                self._sleep(10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                store.set("tg_offset", str(offset))
                try:
                    handler(u)
                except Exception:   # one bad command must never stop the listener
                    log.exception("update %s failed", u.get("update_id"))
