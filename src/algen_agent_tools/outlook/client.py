"""Microsoft Graph mailbox: send, list, read and fetch attachments for one Outlook mailbox."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, cast, get_args
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.auth import OAuthClientCredentials, OAuthRefresh
from algen_agent_tools._core.errors import failure
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.mail.inbound import (
    InboundEmailAttachment,
    InboundEmailDetail,
    InboundEmailSummary,
    bounded_body,
    html_to_text,
)
from algen_agent_tools.mail.models import (
    EmailAddress,
    EmailArtifactLoader,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    EmailMessage,
)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
Folder = Literal["inbox", "sentitems", "drafts", "archive", "junkemail"]
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_SUMMARY_FIELDS = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,bodyPreview,hasAttachments,"
    "categories"
)


class OutlookSettings(BaseModel):
    """One Outlook mailbox through Microsoft Graph.

    With a ``refresh_token`` the connector acts as the signed-in user (delegated access, the
    ``/me`` mailbox); without one it uses the app's own credentials (application access) on the
    mailbox ``mailbox`` names, which the app must be allowed to reach.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=253)
    client_id: str = Field(min_length=1, max_length=1_000)
    client_secret: SecretStr
    refresh_token: SecretStr | None = None
    mailbox: str = Field(min_length=3, max_length=320)
    authority: str = "https://login.microsoftonline.com"
    api_base_url: str = "https://graph.microsoft.com/v1.0"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_attachment_bytes: int = Field(default=3_000_000, ge=1, le=3_500_000)
    """Attachments a send may carry in total: Graph takes them inline only within a 4 MB
    request."""
    max_download_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)
    """The largest attachment ``get_attachment`` reads."""

    @model_validator(mode="after")
    def _check(self) -> OutlookSettings:
        if not _TENANT.fullmatch(self.tenant_id):
            raise ValueError("tenant_id must be a tenant id or domain")
        return self

    @property
    def token_uri(self) -> str:
        return f"{self.authority.rstrip('/')}/{self.tenant_id}/oauth2/v2.0/token"


class OutlookMailboxPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[InboundEmailSummary, ...] = ()
    next_page_token: str | None = None


def _segment(value: str) -> str:
    return quote(value, safe="")


def _address(item: Any) -> str:
    email = (item or {}).get("emailAddress") or {}
    name, address = str(email.get("name") or ""), str(email.get("address") or "")
    return f"{name} <{address}>" if name and name != address else address


def _addresses(items: Any) -> str:
    return ", ".join(_address(item) for item in items or ())


def _received_ms(value: Any) -> int:
    try:
        return max(0, int(datetime.fromisoformat(str(value)).timestamp() * 1000))
    except ValueError:
        return 0


def _recipients(items: tuple[EmailAddress, ...]) -> list[dict[str, Any]]:
    recipients = []
    for item in items:
        email = {"address": item.address}
        if item.display_name:
            email["name"] = item.display_name
        recipients.append({"emailAddress": email})
    return recipients


_PAGE_PARAMETERS = frozenset(
    {"$top", "$select", "$search", "$filter", "$orderby", "$skip", "$skiptoken"}
)


def continuation(folder: Folder, next_link: str | None) -> str | None:
    """An opaque continuation: the folder and Graph's paging parameters, never a URL. The client
    rebuilds the request for its own mailbox, so a token cannot point at another mailbox."""
    if not next_link:
        return None
    parameters = [(k, v) for k, v in parse_qsl(urlsplit(next_link).query) if k in _PAGE_PARAMETERS]
    return f"{folder}?{urlencode(parameters)}"


def _from_page_token(token: str) -> tuple[Folder, dict[str, str]]:
    folder, separator, query = token.partition("?")
    if not separator or folder not in get_args(Folder):
        raise ValueError("page_token is not a page of this mailbox")
    parameters = dict(parse_qsl(query))
    if set(parameters) - _PAGE_PARAMETERS:
        raise ValueError("page_token is not a page of this mailbox")
    return cast(Folder, folder), parameters


class OutlookMailboxClient:
    """Microsoft Graph sender and bounded mailbox reader for one mailbox."""

    def __init__(
        self,
        settings: OutlookSettings,
        *,
        artifact_loader: EmailArtifactLoader | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._artifact_loader = artifact_loader
        self._http = HostLockedClient(
            settings.api_base_url,
            also=(settings.token_uri,),
            timeout_seconds=settings.timeout_seconds,
            # A base64 attachment at the download limit, with headroom for its JSON envelope.
            max_response_bytes=settings.max_download_bytes * 4 // 3 + 1_000_000,
            client=client,
        )
        common: dict[str, Any] = {
            "token_uri": settings.token_uri,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "on_error": lambda exc: EmailDeliveryError(
                failure("Outlook", "OAuth token request", exc)
            ),
        }
        self._oauth: OAuthRefresh | OAuthClientCredentials
        if settings.refresh_token is not None:
            self._oauth = OAuthRefresh(
                self._http,
                refresh_token=settings.refresh_token,
                scope=f"offline_access {GRAPH_SCOPE}",
                **common,
            )
            self._root = "me"
        else:
            self._oauth = OAuthClientCredentials(self._http, scope=GRAPH_SCOPE, **common)
            self._root = f"users/{_segment(settings.mailbox)}"

    def _url(self, path: str) -> str:
        return self._http.url(f"{self._root}/{path}")

    async def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._oauth.token()}", **extra}

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailDeliveryReceipt:
        attachments: list[dict[str, Any]] = []
        total_bytes = 0
        for reference in message.attachments:
            attachments.append(await self._attachment(reference, total_bytes))
            total_bytes += int(attachments[-1].pop("_size"))
        key_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        body: dict[str, Any] = {
            "subject": message.subject,
            "body": (
                {"contentType": "HTML", "content": message.html_body}
                if message.html_body is not None
                else {"contentType": "Text", "content": message.text_body or ""}
            ),
            "toRecipients": _recipients(message.to),
            "ccRecipients": _recipients(message.cc),
            "bccRecipients": _recipients(message.bcc),
            "internetMessageHeaders": [
                {"name": "X-Algen-Idempotency-Key", "value": key_digest},
                *({"name": name, "value": value} for name, value in message.headers.items()),
            ],
        }
        if message.reply_to is not None:
            body["replyTo"] = _recipients((message.reply_to,))
        if attachments:
            body["attachments"] = attachments
        try:
            response = await self._http.post(
                self._url("sendMail"),
                headers=await self._headers(),
                json={"message": body, "saveToSentItems": True},
            )
            response.raise_for_status()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Outlook", "delivery", exc)) from exc
        recipients = tuple(item.address for item in (*message.to, *message.cc, *message.bcc))
        return EmailDeliveryReceipt(
            provider="outlook",
            # Graph accepts a send without returning an id; the key's digest identifies it.
            provider_message_id=f"algen-{key_digest}",
            status=EmailDeliveryStatus.ACCEPTED,
            accepted_recipients=recipients,
        )

    async def _attachment(
        self, reference: EmailAttachmentReference, sent_bytes: int
    ) -> dict[str, Any]:
        if self._artifact_loader is None:
            raise EmailDeliveryError("Outlook attachments require an artifact loader")
        payload = await self._artifact_loader(reference)
        if sent_bytes + len(payload) > self._settings.max_attachment_bytes:
            raise EmailDeliveryError("email attachments exceed the configured size limit")
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": reference.filename,
            "contentType": reference.media_type,
            "contentBytes": base64.b64encode(payload).decode(),
            "_size": len(payload),
        }

    async def _attachments(self, message_id: str) -> tuple[InboundEmailAttachment, ...]:
        response = await self._http.get(
            self._url(f"messages/{_segment(message_id)}/attachments"),
            headers=await self._headers(),
            params={"$select": "id,name,contentType,size"},
        )
        response.raise_for_status()
        return tuple(
            InboundEmailAttachment(
                provider_attachment_id=str(item["id"]),
                filename=re.split(r"[/\\]", str(item.get("name") or "attachment"))[-1]
                or "attachment",
                media_type=str(item.get("contentType") or "application/octet-stream"),
                size=max(0, int(item.get("size") or 0)),
            )
            for item in response.json().get("value", [])[:20]
        )

    async def _summary(self, item: Mapping[str, Any]) -> dict[str, Any]:
        message_id = str(item["id"])
        return {
            "provider_message_id": message_id,
            "thread_id": str(item.get("conversationId") or "") or None,
            "sender": _address(item.get("from")),
            "recipients": _addresses(item.get("toRecipients")),
            "subject": str(item.get("subject") or ""),
            "received_at_ms": _received_ms(item.get("receivedDateTime")),
            "snippet": str(item.get("bodyPreview") or "")[:2_000],
            "labels": tuple(str(label) for label in item.get("categories") or ()),
            "attachments": await self._attachments(message_id)
            if item.get("hasAttachments")
            else (),
        }

    async def list_messages(
        self,
        *,
        folder: Folder = "inbox",
        search: str | None = None,
        unread_only: bool = False,
        max_results: int = 25,
        page_token: str | None = None,
    ) -> OutlookMailboxPage:
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")
        if search and unread_only:
            raise ValueError("search and unread_only cannot be combined")
        try:
            if page_token:
                folder, params = _from_page_token(page_token)
            else:
                params = {"$top": str(max_results), "$select": _SUMMARY_FIELDS}
                if search:
                    params["$search"] = '"' + search.replace('"', "") + '"'
                else:
                    params["$orderby"] = "receivedDateTime desc"
                if unread_only:
                    params["$filter"] = "isRead eq false"
            response = await self._http.get(
                self._url(f"mailFolders/{folder}/messages"),
                headers=await self._headers(),
                params=params,
            )
            response.raise_for_status()
            listing = response.json()
            rows = [InboundEmailSummary(**await self._summary(item)) for item in listing["value"]]
            return OutlookMailboxPage(
                messages=tuple(rows),
                next_page_token=continuation(folder, listing.get("@odata.nextLink")),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Outlook", "inbox read", exc)) from exc

    async def get_message(
        self, provider_message_id: str, *, max_chars: int = 20_000
    ) -> InboundEmailDetail:
        """One message with its body as plain text, cut to ``max_chars``."""
        try:
            response = await self._http.get(
                self._url(f"messages/{_segment(provider_message_id)}"),
                headers=await self._headers(Prefer='outlook.body-content-type="text"'),
                params={"$select": f"{_SUMMARY_FIELDS},ccRecipients,body"},
            )
            response.raise_for_status()
            item = response.json()
            body = item.get("body") or {}
            content = str(body.get("content") or "")
            if str(body.get("contentType") or "").casefold() == "html":
                content = html_to_text(content)
            text, truncated = bounded_body(content.replace("\r\n", "\n").strip(), max_chars)
            return InboundEmailDetail(
                **await self._summary(item),
                cc=_addresses(item.get("ccRecipients")),
                text_body=text,
                body_truncated=truncated,
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmailDeliveryError(failure("Outlook", "message read", exc)) from exc

    async def get_attachment(self, provider_message_id: str, attachment: str) -> AttachmentUpload:
        """One file attachment, with the name and type the message declares for it."""
        try:
            response = await self._http.get(
                self._url(
                    f"messages/{_segment(provider_message_id)}/attachments/{_segment(attachment)}"
                ),
                headers=await self._headers(),
            )
            response.raise_for_status()
            item = response.json()
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                raise EmailDeliveryError("Outlook attachment is not a file")
            if int(item.get("size") or 0) > self._settings.max_download_bytes:
                raise EmailDeliveryError("Outlook attachment exceeds the configured size limit")
            data = base64.b64decode(str(item["contentBytes"]), validate=True)
            name = re.split(r"[/\\]", str(item.get("name") or ""))[-1] or "attachment"
            return AttachmentUpload(
                filename=name,
                media_type=str(item.get("contentType") or "application/octet-stream"),
                data=data,
            )
        except EmailDeliveryError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise EmailDeliveryError(failure("Outlook", "attachment download", exc)) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
