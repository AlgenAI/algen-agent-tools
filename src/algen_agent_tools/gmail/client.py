"""Gmail API mailbox: OAuth refresh-token sending and bounded inbox reads.

Moved from ``algen_agent_runtime.communications.gmail`` (Runtime 0.1.0a11).
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.auth import OAuthRefresh, RefreshTokenSink
from algen_agent_tools._core.errors import failure, not_attempted, rejected
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.mail.inbound import (
    EmailThread,
    InboundEmailAttachment,
    InboundEmailDetail,
    InboundEmailSummary,
    attachment_manifest,
    bounded_body,
    generated_filename,
    html_to_text,
    is_inline_part,
    normalized_content_id,
    thread_headers,
    thread_of,
)
from algen_agent_tools.mail.models import (
    DOMAIN,
    EmailArtifactLoader,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    EmailMessage,
    build_mime_message,
)
from algen_agent_tools.mail.sender_auth import GMAIL_AUTHSERV_ID, topmost_verdict


class GmailOAuthSettings(BaseModel):
    """OAuth refresh-token settings for one Gmail or Google Workspace mailbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    client_id: str = Field(min_length=1, max_length=1_000)
    client_secret: SecretStr
    refresh_token: SecretStr
    token_uri: str = "https://oauth2.googleapis.com/token"
    api_base_url: str = "https://gmail.googleapis.com/gmail/v1"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    message_id_domain: str = Field(default="algen.local", min_length=1, max_length=253)
    max_attachment_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)

    @model_validator(mode="after")
    def validate_settings(self) -> GmailOAuthSettings:
        if not DOMAIN.fullmatch(self.message_id_domain):
            raise ValueError("message_id_domain is invalid")
        return self


def _parts(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Every MIME part of a Gmail message payload, depth first, bounded."""
    pending: list[Mapping[str, Any]] = [payload]
    seen = 0
    while pending and seen < 500:
        part = pending.pop()
        seen += 1
        children = [child for child in part.get("parts", ()) if isinstance(child, Mapping)]
        pending.extend(reversed(children))
        yield part


def _filename(part: Mapping[str, Any]) -> str:
    return re.split(r"[/\\]", str(part.get("filename") or ""))[-1]


def _part_header(part: Mapping[str, Any], name: str) -> str | None:
    for item in part.get("headers") or ():
        if isinstance(item, Mapping) and str(item.get("name", "")).casefold() == name:
            return str(item.get("value", ""))
    return None


def _disposition(part: Mapping[str, Any]) -> str:
    return (_part_header(part, "content-disposition") or "").partition(";")[0].strip().casefold()


def _media_type(part: Mapping[str, Any]) -> str:
    return str(part.get("mimeType") or "application/octet-stream")


def _is_body_text(part: Mapping[str, Any]) -> bool:
    """A part that holds the message's own text rather than a file."""
    return (
        _media_type(part).casefold() in ("text/plain", "text/html")
        and not _filename(part)
        and _disposition(part) != "attachment"
    )


def _attachment_parts(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every leaf part of a message that is a file, named or not, inline or not, in order."""
    found: list[Mapping[str, Any]] = []
    for part in _parts(payload):
        body = part.get("body") or {}
        if (
            part.get("parts")
            or _media_type(part).casefold().startswith("multipart/")
            or not (body.get("attachmentId") or body.get("data"))
            or _is_body_text(part)
            or not (body.get("attachmentId") or part.get("partId"))
        ):
            continue
        found.append(part)
    return found


def _gmail_attachments(payload: Mapping[str, Any]) -> dict[str, Any]:
    attachments: list[InboundEmailAttachment] = []
    for position, part in enumerate(_attachment_parts(payload), 1):
        body = part.get("body") or {}
        media_type = _media_type(part)
        content_id = normalized_content_id(_part_header(part, "content-id"))
        attachments.append(
            InboundEmailAttachment(
                provider_attachment_id=str(body.get("attachmentId") or part.get("partId")),
                filename=_filename(part) or generated_filename(position, media_type),
                media_type=media_type,
                size=max(0, int(body.get("size") or 0)),
                part_id=str(part.get("partId") or "")[:64] or None,
                is_inline=is_inline_part(_disposition(part) or None, content_id),
                content_id=content_id,
            )
        )
    return attachment_manifest(attachments)


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


MAX_TEXT_PARTS = 5


def _body_text(payload: Mapping[str, Any]) -> str:
    """The message's text: its inline text/plain parts in order (at most five, as a client that
    splits the text around inline images sends them), else its first text/html part."""
    found: dict[str, list[str]] = {"text/plain": [], "text/html": []}
    for part in _parts(payload):
        media_type = str(part.get("mimeType") or "").casefold()
        data = (part.get("body") or {}).get("data")
        if media_type in found and data and _is_body_text(part):
            found[media_type].append(_decode(str(data)).decode("utf-8", errors="replace"))
    plain = [text.replace("\r\n", "\n").strip() for text in found["text/plain"][:MAX_TEXT_PARTS]]
    if any(plain):
        return "\n\n".join(text for text in plain if text)
    return html_to_text(found["text/html"][0]) if found["text/html"] else ""


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name", "")).casefold(): str(item.get("value", ""))
        for item in payload.get("payload", {}).get("headers", [])
    }


def _summary_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    email_headers = _headers(payload)
    return {
        "provider_message_id": str(payload["id"]),
        "thread_id": str(payload.get("threadId") or "") or None,
        "sender": email_headers.get("from", ""),
        "recipients": email_headers.get("to", ""),
        "subject": email_headers.get("subject", ""),
        "received_at_ms": max(0, int(payload.get("internalDate", 0))),
        "snippet": str(payload.get("snippet", ""))[:2_000],
        "labels": tuple(str(item) for item in payload.get("labelIds", [])),
        **_gmail_attachments(payload.get("payload", {})),
        "sender_auth": topmost_verdict(
            [
                str(item.get("value", ""))
                for item in payload.get("payload", {}).get("headers", [])
                if str(item.get("name", "")).casefold() == "authentication-results"
            ],
            GMAIL_AUTHSERV_ID,
        ),
    }


def _detail(payload: Mapping[str, Any], max_chars: int) -> InboundEmailDetail:
    email_headers = _headers(payload)
    text, truncated = bounded_body(_body_text(payload.get("payload", {})), max_chars)
    return InboundEmailDetail(
        **_summary_fields(payload),
        cc=email_headers.get("cc", ""),
        text_body=text,
        body_truncated=truncated,
        **thread_headers(
            email_headers.get("message-id", ""),
            email_headers.get("in-reply-to", ""),
            email_headers.get("references", ""),
        ),
    )


def _segment(value: str) -> str:
    """One URL path segment from an id; ids never add path levels."""
    return quote(value, safe="")


class GmailMailboxPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[InboundEmailSummary, ...] = ()
    next_page_token: str | None = None
    result_size_estimate: int = Field(default=0, ge=0)


class GmailMailboxClient:
    """Google Gmail API sender and bounded inbox reader using OAuth refresh tokens."""

    def __init__(
        self,
        settings: GmailOAuthSettings,
        *,
        artifact_loader: EmailArtifactLoader | None = None,
        client: httpx.AsyncClient | None = None,
        on_refresh_token: RefreshTokenSink | None = None,
    ) -> None:
        """``on_refresh_token`` stores a refresh token Google rotated (see ``OAuthRefresh``)."""
        self._settings = settings
        self._artifact_loader = artifact_loader
        self._http = HostLockedClient(
            settings.api_base_url,
            also=(settings.token_uri,),
            timeout_seconds=settings.timeout_seconds,
            # A full message or a base64 attachment at the attachment limit, with headroom.
            max_response_bytes=2 * settings.max_attachment_bytes + 1_000_000,
            client=client,
        )
        self._oauth = OAuthRefresh(
            self._http,
            token_uri=settings.token_uri,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            refresh_token=settings.refresh_token,
            on_error=lambda exc: EmailDeliveryError(failure("Gmail", "OAuth token request", exc)),
            on_rotate=on_refresh_token,
        )

    async def _token(self) -> str:
        return await self._oauth.token()

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailDeliveryReceipt:
        attachments: list[tuple[EmailAttachmentReference, bytes]] = []
        total_bytes = 0
        for reference in message.attachments:
            if self._artifact_loader is None:
                raise not_attempted(
                    EmailDeliveryError("Gmail attachments require an artifact loader")
                )
            payload = await self._artifact_loader(reference)
            total_bytes += len(payload)
            if total_bytes > self._settings.max_attachment_bytes:
                raise not_attempted(
                    EmailDeliveryError("email attachments exceed the configured size limit")
                )
            attachments.append((reference, payload))
        mime, _ = build_mime_message(
            message,
            idempotency_key=idempotency_key,
            attachments=attachments,
            message_id_domain=self._settings.message_id_domain,
            # Gmail takes recipients from the headers and removes Bcc before delivery.
            include_bcc=True,
        )
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode().rstrip("=")
        token = await self._token()
        try:
            response = await self._http.post(
                self._http.url("users/me/messages/send"),
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
            response.raise_for_status()
            provider_id = str(response.json()["id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            error = EmailDeliveryError(failure("Gmail", "delivery", exc))
            if isinstance(exc, httpx.HTTPStatusError) and rejected(exc.response.status_code):
                raise not_attempted(error) from exc  # Gmail refused the message
            raise error from exc
        recipients = tuple(item.address for item in (*message.to, *message.cc, *message.bcc))
        return EmailDeliveryReceipt(
            provider="gmail",
            provider_message_id=provider_id,
            status=EmailDeliveryStatus.ACCEPTED,
            accepted_recipients=recipients,
        )

    async def list_messages(
        self,
        *,
        query: str = "is:unread",
        max_results: int = 25,
        page_token: str | None = None,
    ) -> GmailMailboxPage:
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        token = await self._token()
        headers = {"Authorization": f"Bearer {token}"}
        params: dict[str, str | int] = {"q": query, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = await self._http.get(
                self._http.url("users/me/messages"),
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            listing = response.json()
            rows: list[InboundEmailSummary] = []
            for reference in listing.get("messages", []):
                detail = await self._http.get(
                    self._http.url(f"users/me/messages/{_segment(str(reference['id']))}"),
                    headers=headers,
                    params={
                        "format": "full",
                        "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
                    },
                )
                detail.raise_for_status()
                rows.append(InboundEmailSummary(**_summary_fields(detail.json())))
            return GmailMailboxPage(
                messages=tuple(rows),
                next_page_token=listing.get("nextPageToken"),
                result_size_estimate=max(0, int(listing.get("resultSizeEstimate", len(rows)))),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Gmail", "inbox read", exc)) from exc

    async def _message(self, provider_message_id: str) -> Mapping[str, Any]:
        if not provider_message_id or len(provider_message_id) > 2_000:
            raise ValueError("provider_message_id is invalid")
        token = await self._token()
        response = await self._http.get(
            self._http.url(f"users/me/messages/{_segment(provider_message_id)}"),
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        )
        response.raise_for_status()
        payload: Mapping[str, Any] = response.json()
        return payload

    async def get_message(
        self, provider_message_id: str, *, max_chars: int = 20_000
    ) -> InboundEmailDetail:
        """One message with its body as plain text, cut to ``max_chars``."""
        try:
            return _detail(await self._message(provider_message_id), max_chars)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Gmail", "message read", exc)) from exc

    async def get_thread(
        self, thread_id: str, *, max_messages: int = 20, max_chars: int = 4_000
    ) -> EmailThread:
        """The thread's messages oldest first, the newest ``max_messages`` of them, each body
        cut to ``max_chars``."""
        if not thread_id or len(thread_id) > 2_000:
            raise ValueError("thread_id is invalid")
        try:
            token = await self._token()
            response = await self._http.get(
                self._http.url(f"users/me/threads/{_segment(thread_id)}"),
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
            )
            response.raise_for_status()
            messages = response.json().get("messages", [])
            details = [_detail(payload, max_chars) for payload in messages[-max_messages:]]
            return thread_of(thread_id, details, max_messages, more=len(messages) > max_messages)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Gmail", "thread read", exc)) from exc

    async def get_attachment(self, provider_message_id: str, attachment: str) -> AttachmentUpload:
        """One attachment, named by its part id (stable) or attachment id, with the file name
        and type the message declares for it."""
        try:
            payload = await self._message(provider_message_id)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Gmail", "attachment download", exc)) from exc
        for position, part in enumerate(_attachment_parts(payload.get("payload", {})), 1):
            body = part.get("body") or {}
            attachment_id = str(body.get("attachmentId") or "")
            if attachment not in (str(part.get("partId")), attachment_id):
                continue
            if attachment_id:
                data = await self.download_attachment(provider_message_id, attachment_id)
            else:  # a small part Gmail returns with the message itself
                try:
                    data = _decode(str(body["data"]))
                except (KeyError, ValueError) as exc:
                    raise EmailDeliveryError(failure("Gmail", "attachment download", exc)) from exc
                if len(data) > self._settings.max_attachment_bytes:
                    raise EmailDeliveryError("Gmail attachment exceeds the configured size limit")
            media_type = _media_type(part)
            return AttachmentUpload(
                filename=_filename(part) or generated_filename(position, media_type),
                media_type=media_type,
                data=data,
            )
        raise EmailDeliveryError("Gmail message has no such attachment")

    async def download_attachment(
        self, provider_message_id: str, provider_attachment_id: str
    ) -> bytes:
        if not provider_message_id or len(provider_message_id) > 2_000:
            raise ValueError("provider_message_id is invalid")
        if not provider_attachment_id or len(provider_attachment_id) > 2_000:
            raise ValueError("provider_attachment_id is invalid")
        token = await self._token()
        try:
            response = await self._http.get(
                self._http.url(
                    f"users/me/messages/{_segment(provider_message_id)}"
                    f"/attachments/{_segment(provider_attachment_id)}"
                ),
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            encoded = str(response.json()["data"])
            payload = _decode(encoded)
            if len(payload) > self._settings.max_attachment_bytes:
                raise EmailDeliveryError("Gmail attachment exceeds the configured size limit")
            return payload
        except EmailDeliveryError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Gmail", "attachment download", exc)) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
