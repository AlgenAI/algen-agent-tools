from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from algen_agent_runtime.tools.authoring import Toolkit, read
from pydantic import AwareDatetime, Field, SecretStr

from algen_agent_tools._core.schema import Identifier
from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.attachments import ArtifactSink, AttachmentPolicy
from algen_agent_tools.documents.toolkit import DocumentReader
from algen_agent_tools.mail.models import EmailAddress, EmailArtifactLoader
from algen_agent_tools.mail.tool import (
    AUDIT_METADATA,
    ContextualArtifactLoader,
    MailClient,
    add_mailbox_read_tools,
    add_send_tool,
    bind_artifact_loader,
    parse_domains,
)
from algen_agent_tools.msgraph.credentials import GraphCertificate
from algen_agent_tools.outlook.client import (
    Folder,
    Order,
    OutlookMailboxClient,
    OutlookMailboxPage,
    OutlookSettings,
)

TENANT_ID = "env://ALGEN_TOOLS_OUTLOOK_TENANT_ID"
CLIENT_ID = "env://ALGEN_TOOLS_OUTLOOK_CLIENT_ID"
CLIENT_SECRET = "env://ALGEN_TOOLS_OUTLOOK_CLIENT_SECRET"
REFRESH_TOKEN = "env://ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN"
MAILBOX = "env://ALGEN_TOOLS_OUTLOOK_MAILBOX"
ALLOWED_DOMAINS = "env://ALGEN_TOOLS_OUTLOOK_ALLOWED_DOMAINS"
PRIVATE_KEY = "env://ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY"
PRIVATE_KEY_PASSPHRASE = "env://ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY_PASSPHRASE"
CERTIFICATE = "env://ALGEN_TOOLS_OUTLOOK_CERTIFICATE"
CERTIFICATE_THUMBPRINT = "env://ALGEN_TOOLS_OUTLOOK_CERTIFICATE_THUMBPRINT"
DEFAULT_ENV = env_names(
    TENANT_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    MAILBOX,
    ALLOWED_DOMAINS,
    PRIVATE_KEY,
    PRIVATE_KEY_PASSPHRASE,
    CERTIFICATE,
    CERTIFICATE_THUMBPRINT,
)


async def resolve_certificate(
    secrets: SecretResolver,
    *,
    private_key: str | None,
    private_key_passphrase: str | None,
    certificate: str | None,
    certificate_thumbprint: str | None,
) -> GraphCertificate | None:
    """The certificate credential the references name, or ``None`` when the private key's
    variable is unset. Only references are resolved here; nothing is imported."""
    key = await resolve_optional(secrets, private_key)
    if not key:
        return None
    passphrase = await resolve_optional(secrets, private_key_passphrase)
    return GraphCertificate(
        private_key=SecretStr(key),
        private_key_passphrase=SecretStr(passphrase) if passphrase else None,
        certificate=await resolve_optional(secrets, certificate),
        thumbprint=await resolve_optional(secrets, certificate_thumbprint),
    )


OUTLOOK_ID_PATTERN = r"^[A-Za-z0-9_=+/-][A-Za-z0-9_.=+/-]*$"
"""Graph ids may contain ``/`` and ``+``; the client encodes each id into one path segment."""

# A listing reads each message's attachments in turn, so allow for a full page.
LIST_TIMEOUT_SECONDS = 120


def outlook_toolkit(
    name: str = "outlook",
    *,
    tenant_id: str = TENANT_ID,
    client_id: str = CLIENT_ID,
    client_secret: str | None = CLIENT_SECRET,
    refresh_token: str | None = REFRESH_TOKEN,
    mailbox: str = MAILBOX,
    private_key: str | None = PRIVATE_KEY,
    private_key_passphrase: str | None = PRIVATE_KEY_PASSPHRASE,
    certificate: str | None = CERTIFICATE,
    certificate_thumbprint: str | None = CERTIFICATE_THUMBPRINT,
    allowed_domains: tuple[str, ...] = (),
    allowed_domains_setting: str | None = ALLOWED_DOMAINS,
    artifact_loader: ContextualArtifactLoader | None = None,
    artifact_sink: ArtifactSink | None = None,
    attachment_policy: AttachmentPolicy | None = None,
    documents: DocumentReader | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit for one Outlook (Microsoft 365) mailbox through Microsoft Graph:
    ``<name>.list_messages`` and ``<name>.get_message`` (read), ``<name>.send_email``
    (external) and, with an ``artifact_sink``, ``<name>.get_attachment`` (read).
    With ``documents`` (a :class:`~algen_agent_tools.documents.DocumentReader`),
    ``<name>.read_attachment`` (read) returns an attachment's inspected text in one call, without
    the file reaching the model; with an ``artifact_sink`` too, the file is also saved.

    With ``refresh_token`` set the connector acts as the signed-in user; unset, it uses the app's
    own credentials on ``mailbox``. ``mailbox`` is also the address mail is sent from. Recipients
    are limited as for Gmail (``allowed_domains`` or ``allowed_domains_setting``).

    The app authenticates with a client secret, or, when ``private_key``'s variable is set, a
    certificate: the private key (PEM, optionally encrypted with ``private_key_passphrase``)
    and ``certificate`` (PEM) or ``certificate_thumbprint`` (SHA-1). Set one or the other, not
    both. Certificates need the ``cryptography`` package (the ``outlook`` extra).
    """

    async def open_client(secrets: SecretResolver) -> MailClient:
        delegated = await resolve_optional(secrets, refresh_token)
        address = await secrets(mailbox)
        secret = await resolve_optional(secrets, client_secret)
        credential = await resolve_certificate(
            secrets,
            private_key=private_key,
            private_key_passphrase=private_key_passphrase,
            certificate=certificate,
            certificate_thumbprint=certificate_thumbprint,
        )
        settings = OutlookSettings(
            tenant_id=await secrets(tenant_id),
            client_id=await secrets(client_id),
            client_secret=SecretStr(secret) if secret else None,
            certificate=credential,
            refresh_token=SecretStr(delegated) if delegated else None,
            mailbox=address,
        )
        return MailClient(
            EmailAddress(address=address),
            OutlookMailboxClient(settings, artifact_loader=_bound(artifact_loader)),
            parse_domains(await resolve_optional(secrets, allowed_domains_setting)),
        )

    toolkit = Toolkit(
        name,
        env=env_names(
            tenant_id,
            client_id,
            client_secret,
            refresh_token,
            mailbox,
            allowed_domains_setting,
            private_key,
            private_key_passphrase,
            certificate,
            certificate_thumbprint,
        ),
        client=client or open_client,
    )

    @toolkit.tool(
        read(timeout=LIST_TIMEOUT_SECONDS),
        audit_metadata=AUDIT_METADATA,
        # A mailbox changes on its own: an identical listing later may differ, so it is never
        # served from the result cache or held back as a repeat.
        volatile=True,
    )
    async def list_messages(
        folder: Folder = "inbox",
        search: Annotated[str | None, Field(max_length=500)] = None,
        unread_only: bool = False,
        max_results: Annotated[int, Field(ge=1, le=50)] = 25,
        page_token: Annotated[Identifier | None, Field(max_length=4_000)] = None,
        received_after: AwareDatetime | None = None,
        received_before: AwareDatetime | None = None,
        order: Order = "newest_first",
    ) -> OutlookMailboxPage:
        """List messages in one mailbox folder, newest first by default: sender, recipients,
        subject, a short preview, categories and attachments. search is Outlook search text
        (for example "from:billing@example.com invoice"); unread_only keeps unread messages.
        received_after (inclusive) and received_before (exclusive) are ISO 8601 date-times with
        a time zone that bound when a message was received; order is newest_first or
        oldest_first. search cannot be combined with unread_only, the dates or oldest_first.
        Use next_page_token to continue."""
        mailbox_client: MailClient = await toolkit.client()
        after: datetime | None = received_after
        before: datetime | None = received_before
        page: OutlookMailboxPage = await mailbox_client.transport.list_messages(
            folder=folder,
            search=search,
            unread_only=unread_only,
            max_results=max_results,
            page_token=page_token,
            received_after=after,
            received_before=before,
            order=order,
        )
        return page

    add_mailbox_read_tools(
        toolkit,
        provider="Outlook",
        id_pattern=OUTLOOK_ID_PATTERN,
        artifact_sink=artifact_sink,
        attachment_policy=attachment_policy,
        documents=documents,
    )
    add_send_tool(
        toolkit,
        provider="Outlook",
        attachments=artifact_loader is not None,
        allowed_domains=allowed_domains,
        threading_note="On Outlook the reply is sent as a reply to that message, which must be "
        "in this mailbox; Outlook sets References itself.",
    )
    return toolkit


def _bound(loader: ContextualArtifactLoader | None) -> EmailArtifactLoader | None:
    return bind_artifact_loader(loader) if loader is not None else None
