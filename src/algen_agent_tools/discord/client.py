"""Discord REST API: one bot in one server (guild). List its text channels, read history,
save attachments and post plain text."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools import __version__
from algen_agent_tools._core.errors import failure
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools.attachments.models import AttachmentUpload, check_media_type
from algen_agent_tools.messaging.models import (
    Conversation,
    ConversationPage,
    DeliveryReceipt,
    InboundMessage,
    MessageAttachment,
    MessagePage,
    MessagingError,
    OutgoingMessage,
    Participant,
)

SNOWFLAKE = r"^[0-9]{15,21}$"
_SNOWFLAKE = re.compile(SNOWFLAKE)
_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{30,200}$")
# Channels whose messages the connector reads and writes: text, announcement and threads.
_LISTED_TYPES = frozenset({0, 5})
_MESSAGE_TYPES = _LISTED_TYPES | {10, 11, 12}
SUPPRESS_EMBEDS = 1 << 2


class DiscordSettings(BaseModel):
    """One Discord bot, limited to one server. Channels outside ``guild_id`` (other servers,
    direct messages) are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    bot_token: SecretStr
    guild_id: str
    api_base_url: str = "https://discord.com/api/v10"
    cdn_base_url: str = "https://cdn.discordapp.com"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_download_bytes: int = Field(default=25_000_000, ge=1, le=100_000_000)
    """The largest attachment ``get_attachment`` reads."""

    @model_validator(mode="after")
    def _check(self) -> DiscordSettings:
        if not _TOKEN.fullmatch(self.bot_token.get_secret_value()):
            raise ValueError("bot_token is not a Discord bot token")
        if not _SNOWFLAKE.fullmatch(self.guild_id):
            raise ValueError("guild_id must be a Discord server id")
        return self


def _participant(author: Mapping[str, Any] | None) -> Participant | None:
    if not author:
        return None
    return Participant(
        id=str(author["id"]),
        display_name=author.get("global_name") or None,
        username=author.get("username"),
        is_bot=bool(author.get("bot")),
    )


def _media_type(value: Any) -> str:
    try:
        check_media_type(str(value), "media_type")
    except ValueError:
        return "application/octet-stream"
    return str(value)


def _filename(value: Any) -> str:
    name = re.split(r"[/\\\r\n]", str(value or ""))[-1].strip()
    return name[:255] if name not in ("", ".", "..") else "attachment"


def _attachments(item: Mapping[str, Any]) -> tuple[MessageAttachment, ...]:
    return tuple(
        MessageAttachment(
            attachment_id=str(file["id"]),
            filename=_filename(file.get("filename")),
            media_type=_media_type(file.get("content_type")),
            size_bytes=file.get("size"),
        )
        for file in item.get("attachments") or ()
    )


class DiscordBotClient:
    """A Discord bot's client for one server."""

    def __init__(self, settings: DiscordSettings, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._http = HostLockedClient(
            settings.api_base_url,
            also=(settings.cdn_base_url,),
            timeout_seconds=settings.timeout_seconds,
            max_response_bytes=settings.max_download_bytes + 1_000_000,
            client=client,
        )
        self._headers = {
            "Authorization": f"Bot {settings.bot_token.get_secret_value()}",
            "User-Agent": f"DiscordBot (https://github.com/AlgenAI/algen-agent-tools, {__version__})",
        }
        self._channels: dict[str, Conversation] = {}

    async def _api(self, method: str, path: str, operation: str, **options: Any) -> Any:
        try:
            response = await self._http.request(
                method, self._http.url(path), headers=self._headers, **options
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise MessagingError(failure("Discord", operation, exc)) from exc
        if response.status_code >= 400:
            raise MessagingError(f"Discord {operation} failed (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise MessagingError(failure("Discord", operation, exc)) from exc

    def _conversation(self, channel: Mapping[str, Any]) -> Conversation:
        return Conversation(id=str(channel["id"]), kind="channel", title=channel.get("name"))

    async def _channel(self, channel_id: str) -> Conversation:
        """The channel, once it is known to be a text channel or thread in the configured
        server; anything else is refused."""
        if channel_id in self._channels:
            return self._channels[channel_id]
        if not _SNOWFLAKE.fullmatch(channel_id):
            raise ValueError("channel id must be a Discord id")
        channel = await self._api("GET", f"channels/{channel_id}", "channel lookup")
        if (
            str(channel.get("guild_id")) != self._settings.guild_id
            or channel.get("type") not in _MESSAGE_TYPES
        ):
            raise PolicyDeniedError("the channel is not a text channel of the configured server")
        conversation = self._conversation(channel)
        self._channels[channel_id] = conversation
        return conversation

    async def list_conversations(
        self, *, max_results: int, page_token: str | None
    ) -> ConversationPage:
        """The server's text and announcement channels, in server order."""
        if page_token is not None and not page_token.isdigit():
            raise ValueError("page_token is not a page of this server's channels")
        start = int(page_token or 0)
        channels = await self._api(
            "GET", f"guilds/{self._settings.guild_id}/channels", "channel list"
        )
        try:
            listed = sorted(
                (
                    c
                    for c in channels
                    if c.get("type") in _LISTED_TYPES
                    and str(c.get("guild_id")) == self._settings.guild_id
                ),
                key=lambda c: (int(c.get("position") or 0), int(c["id"])),
            )
            conversations = [self._conversation(c) for c in listed]
        except (KeyError, TypeError, ValueError) as exc:
            raise MessagingError(failure("Discord", "channel list", exc)) from exc
        for conversation in conversations:
            self._channels[conversation.id] = conversation
        end = start + max_results
        return ConversationPage(
            conversations=tuple(conversations[start:end]),
            next_page_token=str(end) if end < len(conversations) else None,
        )

    async def list_messages(
        self, *, conversation_id: str | None, max_results: int, page_token: str | None
    ) -> MessagePage:
        """One channel's messages, newest first; ``page_token`` continues before the oldest
        message of the previous page."""
        if conversation_id is None:
            raise ValueError("Discord needs a conversation_id to list messages")
        if page_token is not None and not _SNOWFLAKE.fullmatch(page_token):
            raise ValueError("page_token is not a page of this channel")
        conversation = await self._channel(conversation_id)
        params: dict[str, Any] = {"limit": max_results}
        if page_token:
            params["before"] = page_token
        items = await self._api(
            "GET", f"channels/{conversation_id}/messages", "message read", params=params
        )
        try:
            messages = tuple(self._message(item, conversation) for item in items)
        except (KeyError, TypeError, ValueError) as exc:
            raise MessagingError(failure("Discord", "message read", exc)) from exc
        full = len(items) == max_results and messages
        return MessagePage(
            messages=messages, next_page_token=messages[-1].message_id if full else None
        )

    @staticmethod
    def _message(item: Mapping[str, Any], conversation: Conversation) -> InboundMessage:
        reference = item.get("message_reference") or {}
        return InboundMessage(
            message_id=str(item["id"]),
            conversation=conversation,
            sender=_participant(item.get("author")),
            sent_at=datetime.fromisoformat(str(item["timestamp"])),
            text=str(item.get("content") or ""),
            edited=bool(item.get("edited_timestamp")),
            reply_to_message_id=(
                str(reference["message_id"]) if reference.get("message_id") else None
            ),
            attachments=_attachments(item),
        )

    async def get_attachment(
        self, conversation_id: str, message_id: str, attachment_id: str
    ) -> AttachmentUpload:
        """The attachment, read from the message so its name, type and CDN URL are Discord's."""
        await self._channel(conversation_id)
        item = await self._api(
            "GET", f"channels/{conversation_id}/messages/{message_id}", "message read"
        )
        file = next(
            (f for f in item.get("attachments") or () if str(f.get("id")) == attachment_id), None
        )
        if file is None:
            raise MessagingError("Discord message has no such attachment")
        if int(file.get("size") or 0) > self._settings.max_download_bytes:
            raise MessagingError("Discord attachment exceeds the configured size limit")
        try:
            # The CDN URL is signed; it needs no token, so none is sent. HostLockedClient
            # refuses it unless it is on the configured CDN.
            response = await self._http.get(str(file["url"]))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise MessagingError(failure("Discord", "attachment download", exc)) from exc
        if response.status_code != 200:
            raise MessagingError(
                f"Discord attachment download failed (HTTP {response.status_code})"
            )
        if len(response.content) > self._settings.max_download_bytes:
            raise MessagingError("Discord attachment exceeds the configured size limit")
        return AttachmentUpload(
            filename=_filename(file.get("filename")),
            media_type=_media_type(file.get("content_type")),
            data=response.content,
        )

    async def send(self, message: OutgoingMessage, *, idempotency_key: str) -> DeliveryReceipt:
        """Post plain text. Mentions never ping (``allowed_mentions`` is empty) and link
        embeds are suppressed. The idempotency key becomes an enforced nonce, so Discord drops
        a repeat of the same send for a few minutes."""
        await self._channel(message.conversation_id)
        payload: dict[str, Any] = {
            "content": message.text,
            "allowed_mentions": {"parse": []},
            "flags": SUPPRESS_EMBEDS,
            "nonce": hashlib.sha256(idempotency_key.encode()).hexdigest()[:25],
            "enforce_nonce": True,
        }
        if message.reply_to_message_id is not None:
            payload["message_reference"] = {
                "message_id": message.reply_to_message_id,
                "fail_if_not_exists": False,
            }
        sent = await self._api(
            "POST", f"channels/{message.conversation_id}/messages", "send", json=payload
        )
        try:
            return DeliveryReceipt(
                provider="discord",
                conversation_id=str(sent["channel_id"]),
                message_id=str(sent["id"]),
                sent_at=datetime.fromisoformat(str(sent["timestamp"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MessagingError(failure("Discord", "send", exc)) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
