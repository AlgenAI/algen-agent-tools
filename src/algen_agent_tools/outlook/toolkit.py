from __future__ import annotations

from typing import Annotated, Any

from algen_agent_runtime.tools.authoring import Toolkit, read
from pydantic import Field, SecretStr

from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.attachments import ArtifactSink, AttachmentPolicy
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
from algen_agent_tools.outlook.client import (
    Folder,
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
DEFAULT_ENV = env_names(
    TENANT_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, MAILBOX, ALLOWED_DOMAINS
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
    client_secret: str = CLIENT_SECRET,
    refresh_token: str | None = REFRESH_TOKEN,
    mailbox: str = MAILBOX,
    allowed_domains: tuple[str, ...] = (),
    allowed_domains_setting: str | None = ALLOWED_DOMAINS,
    artifact_loader: ContextualArtifactLoader | None = None,
    artifact_sink: ArtifactSink | None = None,
    attachment_policy: AttachmentPolicy | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit for one Outlook (Microsoft 365) mailbox through Microsoft Graph:
    ``<name>.list_messages`` and ``<name>.get_message`` (read), ``<name>.send_email``
    (external) and, with an ``artifact_sink``, ``<name>.get_attachment`` (read).

    With ``refresh_token`` set the connector acts as the signed-in user; unset, it uses the app's
    own credentials on ``mailbox``. ``mailbox`` is also the address mail is sent from. Recipients
    are limited as for Gmail (``allowed_domains`` or ``allowed_domains_setting``).
    """

    async def open_client(secrets: SecretResolver) -> MailClient:
        delegated = await resolve_optional(secrets, refresh_token)
        address = await secrets(mailbox)
        settings = OutlookSettings(
            tenant_id=await secrets(tenant_id),
            client_id=await secrets(client_id),
            client_secret=SecretStr(await secrets(client_secret)),
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
            tenant_id, client_id, client_secret, refresh_token, mailbox, allowed_domains_setting
        ),
        client=client or open_client,
    )

    @toolkit.tool(read(timeout=LIST_TIMEOUT_SECONDS), audit_metadata=AUDIT_METADATA)
    async def list_messages(
        folder: Folder = "inbox",
        search: Annotated[str | None, Field(max_length=500)] = None,
        unread_only: bool = False,
        max_results: Annotated[int, Field(ge=1, le=50)] = 25,
        page_token: Annotated[str | None, Field(max_length=4_000)] = None,
    ) -> OutlookMailboxPage:
        """List messages in one mailbox folder, newest first: sender, recipients, subject, a
        short preview, categories and attachments. search is Outlook search text (for example
        "from:billing@example.com invoice"); unread_only keeps unread messages and cannot be
        combined with search. Use next_page_token to continue."""
        mailbox_client: MailClient = await toolkit.client()
        page: OutlookMailboxPage = await mailbox_client.transport.list_messages(
            folder=folder,
            search=search,
            unread_only=unread_only,
            max_results=max_results,
            page_token=page_token,
        )
        return page

    add_mailbox_read_tools(
        toolkit,
        provider="Outlook",
        id_pattern=OUTLOOK_ID_PATTERN,
        artifact_sink=artifact_sink,
        attachment_policy=attachment_policy,
    )
    add_send_tool(
        toolkit,
        provider="Outlook",
        attachments=artifact_loader is not None,
        allowed_domains=allowed_domains,
    )
    return toolkit


def _bound(loader: ContextualArtifactLoader | None) -> EmailArtifactLoader | None:
    return bind_artifact_loader(loader) if loader is not None else None
