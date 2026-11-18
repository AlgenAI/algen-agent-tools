"""Microsoft Graph mailbox: send, list, read and fetch attachments for one Outlook mailbox."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast, get_args
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.auth import OAuthClientCredentials, OAuthRefresh, RefreshTokenSink
from algen_agent_tools._core.errors import failure, not_attempted, rejected
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools._core.schema import Identifier
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.mail.inbound import (
    MAX_THREAD_MESSAGES,
    EmailThread,
    InboundEmailAttachment,
    InboundEmailDetail,
    InboundEmailSummary,
    attachment_manifest,
    bounded_body,
    generated_filename,
    html_to_text,
    normalized_content_id,
    thread_headers,
    thread_of,
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
from algen_agent_tools.msgraph.credentials import (
    GraphCertificate,
    check_one_credential,
    client_authentication,
)
from algen_agent_tools.msgraph.throttling import throttled, throttled_response

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
Folder = Literal["inbox", "sentitems", "drafts", "archive", "junkemail"]
Order = Literal["newest_first", "oldest_first"]
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_SUMMARY_FIELDS = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,bodyPreview,hasAttachments,"
    "categories"
)
_DETAIL_FIELDS = f"{_SUMMARY_FIELDS},ccRecipients,body,internetMessageId,internetMessageHeaders"
_TEXT_BODY = 'outlook.body-content-type="text"'
_ATTACHMENT_FIELDS = "id,name,contentType,size,isInline"
_ATTACHMENT_PAGES = 5
"""The most pages of attachment descriptors a summary reads to count a message's attachments."""


def _name(item: Mapping[str, Any]) -> str:
    """An attachment's declared file name without any path, or an empty string."""
    return re.split(r"[/\\]", str(item.get("name") or ""))[-1].strip()


class OutlookSettings(BaseModel):
    """One Outlook mailbox through Microsoft Graph.

    With a ``refresh_token`` the connector acts as the signed-in user (delegated access, the
    ``/me`` mailbox); without one it uses the app's own credentials (application access) on the
    mailbox ``mailbox`` names, which the app must be allowed to reach.

    The app authenticates with exactly one of ``client_secret`` and ``certificate`` (a
    :class:`~algen_agent_tools.msgraph.GraphCertificate`, which signs a client assertion), in
    either access mode.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=253)
    client_id: str = Field(min_length=1, max_length=1_000)
    client_secret: SecretStr | None = None
    certificate: GraphCertificate | None = None
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
        check_one_credential(self.client_secret, self.certificate)
        return self

    @property
    def token_uri(self) -> str:
        return f"{self.authority.rstrip('/')}/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def credential_kind(self) -> Literal["certificate", "client_secret"]:
        return "certificate" if self.certificate is not None else "client_secret"


class OutlookMailboxPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[InboundEmailSummary, ...] = ()
    next_page_token: Identifier | None = None


def _segment(value: str) -> str:
    return quote(value, safe="")


def _address(item: Any) -> str:
    email = (item or {}).get("emailAddress") or {}
    name, address = str(email.get("name") or ""), str(email.get("address") or "")
    return f"{name} <{address}>" if name and name != address else address


def _addresses(items: Any) -> str:
    return ", ".join(_address(item) for item in items or ())


def _odata_datetime(value: datetime, field: str) -> str:
    """An aware datetime as an OData ``DateTimeOffset`` literal in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def received_filter(
    received_after: datetime | None = None,
    received_before: datetime | None = None,
    *,
    unread_only: bool = False,
) -> str | None:
    """The ``$filter`` for a received-date window (``after`` inclusive, ``before`` exclusive)
    and unread mail. The ``receivedDateTime`` terms come first, as Graph requires when the
    listing is also ordered by ``receivedDateTime``."""
    terms: list[str] = []
    if received_after is not None:
        terms.append(f"receivedDateTime ge {_odata_datetime(received_after, 'received_after')}")
    if received_before is not None:
        terms.append(f"receivedDateTime lt {_odata_datetime(received_before, 'received_before')}")
    if received_after is not None and received_before is not None:
        if received_after >= received_before:
            raise ValueError("received_after must be before received_before")
    if unread_only:
        terms.append("isRead eq false")
    return " and ".join(terms) or None


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


def _read_error(operation: str, cause: BaseException) -> EmailDeliveryError:
    """A read's failure, naming only the cause's type. Throttling (429, 503) is also a
    :class:`~algen_agent_tools.msgraph.GraphThrottled` with Graph's ``Retry-After`` wait."""
    error = EmailDeliveryError(failure("Outlook", operation, cause))
    response = throttled_response(cause)
    if response is not None:
        return not_attempted(throttled(error, response))
    return error


class OutlookMailboxClient:
    """Microsoft Graph sender and bounded mailbox reader for one mailbox."""

    def __init__(
        self,
        settings: OutlookSettings,
        *,
        artifact_loader: EmailArtifactLoader | None = None,
        client: httpx.AsyncClient | None = None,
        on_refresh_token: RefreshTokenSink | None = None,
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
            # A certificate's key is loaded here: a wrong key or passphrase fails on open.
            "client_auth": client_authentication(
                client_id=settings.client_id,
                token_uri=settings.token_uri,
                client_secret=settings.client_secret,
                certificate=settings.certificate,
                extra="outlook",
            ),
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
                on_rotate=on_refresh_token,
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
        """Send ``message``. With ``in_reply_to`` it is a reply (``messages/{id}/reply``) to the
        mailbox's message with that Message-ID, so Graph sets In-Reply-To and References from
        the original and keeps the reply in its conversation. ``references`` is not sent: Graph
        builds it, and takes only ``X-`` headers on a new message."""
        if message.thread_id is not None:
            raise not_attempted(
                EmailDeliveryError(
                    "Outlook threads a reply by in_reply_to; thread_id is not supported"
                )
            )
        if message.references and message.in_reply_to is None:
            raise not_attempted(
                EmailDeliveryError(
                    "Outlook cannot set References on a new message; reply with in_reply_to"
                )
            )
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
        if message.in_reply_to is not None:
            original = await self._message_by_header(message.in_reply_to)
            url = self._url(f"messages/{_segment(original)}/reply")
            payload: dict[str, Any] = {"message": body}
        else:
            url = self._url("sendMail")
            payload = {"message": body, "saveToSentItems": True}
        try:
            response = await self._http.post(url, headers=await self._headers(), json=payload)
            response.raise_for_status()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            error = EmailDeliveryError(failure("Outlook", "delivery", exc))
            if isinstance(exc, httpx.HTTPStatusError) and rejected(exc.response.status_code):
                raise not_attempted(error) from exc  # Graph refused the message
            raise error from exc
        recipients = tuple(item.address for item in (*message.to, *message.cc, *message.bcc))
        return EmailDeliveryReceipt(
            provider="outlook",
            # Graph accepts a send without returning an id; the key's digest identifies it.
            provider_message_id=f"algen-{key_digest}",
            status=EmailDeliveryStatus.ACCEPTED,
            accepted_recipients=recipients,
        )

    async def _message_by_header(self, message_id: str) -> str:
        """The Graph id of this mailbox's message whose Message-ID is ``message_id``."""
        # OData quotes a literal in single quotes, doubling any inside it.
        literal = message_id.replace("'", "''")
        try:
            response = await self._http.get(
                self._url("messages"),
                headers=await self._headers(),
                params={
                    "$filter": f"internetMessageId eq '{literal}'",
                    "$select": "id",
                    "$top": "1",
                },
            )
            response.raise_for_status()
            found = response.json()["value"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise not_attempted(
                EmailDeliveryError(failure("Outlook", "reply lookup", exc))
            ) from exc
        if not found or not str(found[0].get("id") or ""):
            raise not_attempted(
                EmailDeliveryError("Outlook has no message with that in_reply_to in this mailbox")
            )
        return str(found[0]["id"])

    async def _attachment(
        self, reference: EmailAttachmentReference, sent_bytes: int
    ) -> dict[str, Any]:
        if self._artifact_loader is None:
            raise not_attempted(
                EmailDeliveryError("Outlook attachments require an artifact loader")
            )
        payload = await self._artifact_loader(reference)
        if sent_bytes + len(payload) > self._settings.max_attachment_bytes:
            raise not_attempted(
                EmailDeliveryError("email attachments exceed the configured size limit")
            )
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": reference.filename,
            "contentType": reference.media_type,
            "contentBytes": base64.b64encode(payload).decode(),
            "_size": len(payload),
        }

    async def _attachment_items(self, message_id: str) -> tuple[list[Mapping[str, Any]], bool]:
        """The message's attachment descriptors, in Graph's order, following at most
        :data:`_ATTACHMENT_PAGES` pages, and whether Graph had more."""
        url = self._url(f"messages/{_segment(message_id)}/attachments")
        params: dict[str, str] | None = {"$select": _ATTACHMENT_FIELDS}
        items: list[Mapping[str, Any]] = []
        for _ in range(_ATTACHMENT_PAGES):
            response = await self._http.get(url, headers=await self._headers(), params=params)
            response.raise_for_status()
            listing = response.json()
            items.extend(item for item in listing.get("value", []) if isinstance(item, Mapping))
            next_link = listing.get("@odata.nextLink")
            if not next_link:
                return items, False
            url, params = str(next_link), None  # Graph's link carries the query
        return items, True

    async def _attachments(self, message_id: str) -> dict[str, Any]:
        items, more = await self._attachment_items(message_id)
        found: list[InboundEmailAttachment] = []
        for position, item in enumerate(items, 1):
            media_type = str(item.get("contentType") or "application/octet-stream")
            content_id = normalized_content_id(str(item.get("contentId") or ""))
            found.append(
                InboundEmailAttachment(
                    provider_attachment_id=str(item["id"]),
                    filename=_name(item) or generated_filename(position, media_type),
                    media_type=media_type,
                    size=max(0, int(item.get("size") or 0)),
                    is_inline=bool(item.get("isInline")),
                    content_id=content_id,
                )
            )
        manifest = attachment_manifest(found)
        if more:  # Graph has further pages: the count is a lower bound
            manifest["attachments_truncated"] = True
        return manifest

    async def _summary(
        self, item: Mapping[str, Any], *, inline_too: bool = False
    ) -> dict[str, Any]:
        """``inline_too`` reads the attachments even when ``hasAttachments`` is false, which Graph
        reports for a message whose only attachments are inline (a pasted picture, or a PDF
        Apple Mail marked inline). A listing does not, to keep to one request per message."""
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
            **(
                await self._attachments(message_id)
                if inline_too or item.get("hasAttachments")
                else {}
            ),
        }

    async def list_messages(
        self,
        *,
        folder: Folder = "inbox",
        search: str | None = None,
        unread_only: bool = False,
        max_results: int = 25,
        page_token: str | None = None,
        received_after: datetime | None = None,
        received_before: datetime | None = None,
        order: Order = "newest_first",
    ) -> OutlookMailboxPage:
        """One page of a folder. ``received_after`` (inclusive) and ``received_before``
        (exclusive) are timezone-aware datetimes that bound ``receivedDateTime``; ``order``
        sorts by it. Graph cannot combine ``search`` with a filter or an order, so ``search``
        is refused with ``unread_only``, a date window or ``oldest_first``. A ``page_token``
        carries the original request's filter and order."""
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")
        if search and unread_only:
            raise ValueError("search and unread_only cannot be combined")
        if search and (received_after or received_before or order != "newest_first"):
            raise ValueError(
                "search cannot be combined with received_after, received_before or "
                "oldest_first: Graph does not filter or order a search"
            )
        if order not in get_args(Order):
            raise ValueError("order must be newest_first or oldest_first")
        date_filter = received_filter(received_after, received_before, unread_only=unread_only)
        try:
            if page_token:
                folder, params = _from_page_token(page_token)
            else:
                params = {"$top": str(max_results), "$select": _SUMMARY_FIELDS}
                if search:
                    params["$search"] = '"' + search.replace('"', "") + '"'
                else:
                    direction = "asc" if order == "oldest_first" else "desc"
                    params["$orderby"] = f"receivedDateTime {direction}"
                if date_filter:
                    params["$filter"] = date_filter
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
            raise _read_error("inbox read", exc) from exc

    async def _detail(self, item: Mapping[str, Any], max_chars: int) -> InboundEmailDetail:
        body = item.get("body") or {}
        content = str(body.get("content") or "")
        if str(body.get("contentType") or "").casefold() == "html":
            content = html_to_text(content)
        text, truncated = bounded_body(content.replace("\r\n", "\n").strip(), max_chars)
        # Graph gives In-Reply-To and References only among the message's internet headers.
        headers = {
            str(header.get("name") or "").casefold(): str(header.get("value") or "")
            for header in item.get("internetMessageHeaders") or ()
        }
        return InboundEmailDetail(
            **await self._summary(item, inline_too=True),
            cc=_addresses(item.get("ccRecipients")),
            text_body=text,
            body_truncated=truncated,
            **thread_headers(
                str(item.get("internetMessageId") or headers.get("message-id", "")),
                headers.get("in-reply-to", ""),
                headers.get("references", ""),
            ),
        )

    async def get_message(
        self, provider_message_id: str, *, max_chars: int = 20_000
    ) -> InboundEmailDetail:
        """One message with its body as plain text, cut to ``max_chars``."""
        try:
            response = await self._http.get(
                self._url(f"messages/{_segment(provider_message_id)}"),
                headers=await self._headers(Prefer=_TEXT_BODY),
                params={"$select": _DETAIL_FIELDS},
            )
            response.raise_for_status()
            return await self._detail(response.json(), max_chars)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise _read_error("message read", exc) from exc

    async def get_thread(
        self, thread_id: str, *, max_messages: int = 20, max_chars: int = 4_000
    ) -> EmailThread:
        """The conversation's messages oldest first, the newest ``max_messages`` of the first
        50 Graph returns, each body cut to ``max_chars``."""
        if not thread_id or len(thread_id) > 2_000 or "'" in thread_id:
            raise ValueError("thread_id is invalid")
        try:
            # Graph refuses to order a conversationId filter, so the messages are sorted here.
            response = await self._http.get(
                self._url("messages"),
                headers=await self._headers(Prefer=_TEXT_BODY),
                params={
                    "$filter": f"conversationId eq '{thread_id}'",
                    "$select": _DETAIL_FIELDS,
                    "$top": str(MAX_THREAD_MESSAGES),
                },
            )
            response.raise_for_status()
            listing = response.json()
            items = sorted(
                listing["value"], key=lambda item: _received_ms(item.get("receivedDateTime"))
            )
            if not items:
                raise EmailDeliveryError("Outlook conversation has no messages in this mailbox")
            details = [await self._detail(item, max_chars) for item in items[-max_messages:]]
            more = len(items) > max_messages or bool(listing.get("@odata.nextLink"))
            return thread_of(thread_id, details, max_messages, more=more)
        except EmailDeliveryError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise _read_error("thread read", exc) from exc

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
            media_type = str(item.get("contentType") or "application/octet-stream")
            name = _name(item)
            if not name:  # the name the listing gave it, from its place in the message
                items, _ = await self._attachment_items(provider_message_id)
                ids = [str(entry.get("id")) for entry in items]
                position = ids.index(attachment) + 1 if attachment in ids else 1
                name = generated_filename(position, media_type)
            return AttachmentUpload(filename=name, media_type=media_type, data=data)
        except EmailDeliveryError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise _read_error("attachment download", exc) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
