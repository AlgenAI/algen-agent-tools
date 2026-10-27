"""Telegram Bot API: read the messages a bot receives, save their files and post plain text."""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.errors import failure
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools.attachments.models import AttachmentUpload, check_media_type
from algen_agent_tools.messaging.models import (
    Conversation,
    ConversationKind,
    DeliveryReceipt,
    InboundMessage,
    MessageAttachment,
    MessagePage,
    MessagingError,
    OutgoingMessage,
    Participant,
)

_TOKEN = re.compile(r"^[0-9]{1,20}:[A-Za-z0-9_-]{20,200}$")
_FILE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
_KINDS: dict[str, ConversationKind] = {
    "private": "direct",
    "group": "group",
    "supergroup": "group",
    "channel": "channel",
}
_UPDATE_KINDS = (
    ("message", False),
    ("edited_message", True),
    ("channel_post", False),
    ("edited_channel_post", True),
)
# Kinds of file a message can carry: (field, default file name, default media type).
_FILES = (
    ("document", "document", "application/octet-stream"),
    ("audio", "audio.mp3", "audio/mpeg"),
    ("video", "video.mp4", "video/mp4"),
    ("animation", "animation.mp4", "video/mp4"),
    ("voice", "voice.ogg", "audio/ogg"),
    ("video_note", "video_note.mp4", "video/mp4"),
)


class TelegramSettings(BaseModel):
    """One Telegram bot. The token is part of every Bot API URL, so it is kept out of errors
    and redacted from ``httpx`` request logs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    bot_token: SecretStr
    api_base_url: str = "https://api.telegram.org"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_download_bytes: int = Field(default=20_000_000, ge=1, le=20_000_000)
    """The largest file ``get_attachment`` reads; the Bot API serves at most 20 MB."""

    @model_validator(mode="after")
    def _check(self) -> TelegramSettings:
        if not _TOKEN.fullmatch(self.bot_token.get_secret_value()):
            raise ValueError("bot_token is not a Telegram bot token")
        return self


def _participant(user: Mapping[str, Any] | None) -> Participant | None:
    if not user:
        return None
    name = " ".join(str(user[k]) for k in ("first_name", "last_name") if user.get(k))
    return Participant(
        id=str(user["id"]),
        display_name=name or None,
        username=user.get("username"),
        is_bot=bool(user.get("is_bot")),
    )


def _conversation(chat: Mapping[str, Any]) -> Conversation:
    title = chat.get("title")
    if not title:
        title = " ".join(str(chat[k]) for k in ("first_name", "last_name") if chat.get(k))
    return Conversation(
        id=str(chat["id"]), kind=_KINDS.get(str(chat.get("type")), "group"), title=title or None
    )


def _media_type(value: Any, default: str) -> str:
    try:
        check_media_type(str(value), "media_type")
    except ValueError:
        return default
    return str(value)


def _filename(value: Any, default: str) -> str:
    name = re.split(r"[/\\\r\n]", str(value or ""))[-1].strip()
    return name[:255] if name not in ("", ".", "..") else default


def _attachments(item: Mapping[str, Any]) -> tuple[MessageAttachment, ...]:
    found = []
    if item.get("photo"):
        largest = item["photo"][-1]
        found.append(
            MessageAttachment(
                attachment_id=str(largest["file_id"]),
                filename="photo.jpg",
                media_type="image/jpeg",
                size_bytes=largest.get("file_size"),
            )
        )
    for key, name, media_type in _FILES:
        file = item.get(key)
        if file:
            found.append(
                MessageAttachment(
                    attachment_id=str(file["file_id"]),
                    filename=_filename(file.get("file_name"), name),
                    media_type=_media_type(file.get("mime_type"), media_type),
                    size_bytes=file.get("file_size"),
                )
            )
    return tuple(found)


def _message(item: Mapping[str, Any], edited: bool) -> InboundMessage:
    reply = item.get("reply_to_message") or {}
    return InboundMessage(
        message_id=str(item["message_id"]),
        conversation=_conversation(item["chat"]),
        sender=_participant(item.get("from")),
        sent_at=datetime.fromtimestamp(int(item["date"]), UTC),
        text=str(item.get("text") or item.get("caption") or ""),
        edited=edited,
        reply_to_message_id=str(reply["message_id"]) if reply.get("message_id") else None,
        attachments=_attachments(item),
    )


class TelegramBotClient:
    """A Telegram bot's client: pending updates, file downloads and ``sendMessage``."""

    def __init__(
        self, settings: TelegramSettings, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        token = settings.bot_token.get_secret_value()
        self._http = HostLockedClient(
            settings.api_base_url,
            timeout_seconds=settings.timeout_seconds,
            max_response_bytes=settings.max_download_bytes + 1_000_000,
            client=client,
            url_secrets=(token,),
        )
        self._method_root = f"bot{token}"
        self._file_root = f"file/bot{token}"

    async def _call(self, method: str, operation: str, payload: dict[str, Any]) -> Any:
        # Errors never chain the httpx exception: its message carries the URL, and the URL
        # carries the token.
        try:
            response = await self._http.post(
                self._http.url(f"{self._method_root}/{method}"), json=payload
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MessagingError(failure("Telegram", operation, exc)) from None
        if not isinstance(body, dict) or not body.get("ok"):
            code = body.get("error_code") if isinstance(body, dict) else None
            detail = f"error {code}" if isinstance(code, int) else f"HTTP {response.status_code}"
            if code == 409:
                detail += ": another getUpdates consumer or a webhook is active"
            raise MessagingError(f"Telegram {operation} failed ({detail})")
        return body.get("result")

    async def list_conversations(self, *, max_results: int, page_token: str | None) -> Any:
        raise NotImplementedError("Telegram bots cannot list their chats")

    async def list_messages(
        self, *, conversation_id: str | None, max_results: int, page_token: str | None
    ) -> MessagePage:
        """The bot's pending updates, newest first. No ``offset`` is sent, so reading never
        confirms (and so deletes) an update."""
        if page_token is not None:
            raise ValueError("Telegram returns a single page; page_token is not supported")
        updates = await self._call("getUpdates", "update read", {"limit": 100, "timeout": 0})
        try:
            messages = [
                _message(update[key], edited)
                for update in reversed(updates)
                for key, edited in _UPDATE_KINDS
                if update.get(key)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise MessagingError(failure("Telegram", "update read", exc)) from None
        if conversation_id is not None:
            messages = [m for m in messages if m.conversation.id == conversation_id]
        return MessagePage(messages=tuple(messages[:max_results]))

    async def get_attachment(
        self, conversation_id: str, message_id: str, attachment_id: str
    ) -> AttachmentUpload:
        """The file ``attachment_id`` (a Bot API ``file_id``) names. The Bot API cannot read a
        message by id, so the file is not checked against ``message_id``."""
        file = await self._call("getFile", "file lookup", {"file_id": attachment_id})
        path = str((file or {}).get("file_path") or "")
        if not _FILE_PATH.fullmatch(path) or ".." in path.split("/"):
            raise MessagingError("Telegram file lookup returned an unusable path")
        if int(file.get("file_size") or 0) > self._settings.max_download_bytes:
            raise MessagingError("Telegram file exceeds the configured size limit")
        try:
            response = await self._http.get(
                self._http.url(f"{self._file_root}/{quote(path, safe='/')}")
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise MessagingError(failure("Telegram", "file download", exc)) from None
        if response.status_code != 200:
            raise MessagingError(f"Telegram file download failed (HTTP {response.status_code})")
        if len(response.content) > self._settings.max_download_bytes:
            raise MessagingError("Telegram file exceeds the configured size limit")
        filename = _filename(path, "attachment")
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return AttachmentUpload(filename=filename, media_type=media_type, data=response.content)

    async def send(self, message: OutgoingMessage, *, idempotency_key: str) -> DeliveryReceipt:
        """Post ``message`` as plain text with link previews off. The Bot API has no
        idempotency key: the Runtime's single attempt for external actions is the guard."""
        payload: dict[str, Any] = {
            "chat_id": int(message.conversation_id),
            "text": message.text,
            "link_preview_options": {"is_disabled": True},
        }
        if message.reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": int(message.reply_to_message_id),
                "allow_sending_without_reply": True,
            }
        sent = await self._call("sendMessage", "send", payload)
        try:
            return DeliveryReceipt(
                provider="telegram",
                conversation_id=str(sent["chat"]["id"]),
                message_id=str(sent["message_id"]),
                sent_at=datetime.fromtimestamp(int(sent["date"]), UTC),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MessagingError(failure("Telegram", "send", exc)) from None

    async def aclose(self) -> None:
        await self._http.aclose()
