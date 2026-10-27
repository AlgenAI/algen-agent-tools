from __future__ import annotations

from typing import Annotated, Any

from algen_agent_runtime.tools.authoring import Toolkit, read
from pydantic import Field, SecretStr

from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.gmail.client import GmailMailboxClient, GmailMailboxPage, GmailOAuthSettings
from algen_agent_tools.mail.models import EmailAddress, EmailArtifactLoader
from algen_agent_tools.mail.tool import (
    AUDIT_METADATA,
    ContextualArtifactLoader,
    MailClient,
    add_send_tool,
    bind_artifact_loader,
    parse_domains,
)

CLIENT_ID = "env://ALGEN_TOOLS_GMAIL_CLIENT_ID"
CLIENT_SECRET = "env://ALGEN_TOOLS_GMAIL_CLIENT_SECRET"
REFRESH_TOKEN = "env://ALGEN_TOOLS_GMAIL_REFRESH_TOKEN"
SENDER = "env://ALGEN_TOOLS_GMAIL_SENDER"
ALLOWED_DOMAINS = "env://ALGEN_TOOLS_GMAIL_ALLOWED_DOMAINS"
DEFAULT_ENV = env_names(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, SENDER, ALLOWED_DOMAINS)

# A Gmail list call fetches each message's metadata in turn, so allow for a full page.
LIST_TIMEOUT_SECONDS = 120


def gmail_toolkit(
    name: str = "gmail",
    *,
    client_id: str = CLIENT_ID,
    client_secret: str = CLIENT_SECRET,
    refresh_token: str = REFRESH_TOKEN,
    sender: str = SENDER,
    allowed_domains: tuple[str, ...] = (),
    allowed_domains_setting: str | None = ALLOWED_DOMAINS,
    artifact_loader: ContextualArtifactLoader | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit for one Gmail or Google Workspace mailbox: ``<name>.list_messages`` (read)
    and ``<name>.send_email`` (external).

    The OAuth client and refresh token are ``env://`` references resolved when the client
    opens. ``sender`` is the mailbox's address (or a verified alias). Recipients are limited to
    ``allowed_domains`` or, when that is empty, to the comma-separated domains in
    ``allowed_domains_setting`` (unset: any domain).
    """

    async def open_client(secrets: SecretResolver) -> MailClient:
        settings = GmailOAuthSettings(
            client_id=await secrets(client_id),
            client_secret=SecretStr(await secrets(client_secret)),
            refresh_token=SecretStr(await secrets(refresh_token)),
        )
        return MailClient(
            EmailAddress(address=await secrets(sender)),
            GmailMailboxClient(settings, artifact_loader=_bound(artifact_loader)),
            parse_domains(await resolve_optional(secrets, allowed_domains_setting)),
        )

    toolkit = Toolkit(
        name,
        env=env_names(client_id, client_secret, refresh_token, sender, allowed_domains_setting),
        client=client or open_client,
    )

    @toolkit.tool(read(timeout=LIST_TIMEOUT_SECONDS), audit_metadata=AUDIT_METADATA)
    async def list_messages(
        query: Annotated[str, Field(max_length=500)] = "is:unread",
        max_results: Annotated[int, Field(ge=1, le=100)] = 25,
        page_token: Annotated[str | None, Field(max_length=2_000)] = None,
    ) -> GmailMailboxPage:
        """List messages in the mailbox that match a Gmail search query (for example
        "is:unread from:billing@example.com newer_than:7d"): sender, recipients, subject, a
        short snippet, labels and attachment names, newest first. Use next_page_token to
        continue."""
        mailbox: MailClient = await toolkit.client()
        page: GmailMailboxPage = await mailbox.transport.list_messages(
            query=query, max_results=max_results, page_token=page_token
        )
        return page

    add_send_tool(
        toolkit,
        provider="Gmail",
        attachments=artifact_loader is not None,
        allowed_domains=allowed_domains,
    )
    return toolkit


def _bound(loader: ContextualArtifactLoader | None) -> EmailArtifactLoader | None:
    return bind_artifact_loader(loader) if loader is not None else None
