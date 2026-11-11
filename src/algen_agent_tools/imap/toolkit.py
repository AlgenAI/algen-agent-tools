from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from algen_agent_runtime.exceptions.errors import ConfigurationError
from algen_agent_runtime.tools.authoring import Toolkit, read
from pydantic import Field, SecretStr, ValidationError

from algen_agent_tools._core.schema import Identifier
from algen_agent_tools._env import SecretResolver, env_names, resolve_optional, split_list
from algen_agent_tools.attachments import ArtifactSink, AttachmentPolicy
from algen_agent_tools.documents.toolkit import DocumentReader
from algen_agent_tools.imap.client import (
    ImapFactory,
    ImapFolderList,
    ImapMailboxClient,
    ImapMailboxPage,
    ImapSettings,
)
from algen_agent_tools.mail.tool import AUDIT_METADATA, add_mailbox_read_tools

HOST = "env://ALGEN_TOOLS_IMAP_HOST"
PORT = "env://ALGEN_TOOLS_IMAP_PORT"
USERNAME = "env://ALGEN_TOOLS_IMAP_USERNAME"
PASSWORD = "env://ALGEN_TOOLS_IMAP_PASSWORD"
FOLDERS = "env://ALGEN_TOOLS_IMAP_FOLDERS"
AUTHSERV_ID = "env://ALGEN_TOOLS_IMAP_AUTHSERV_ID"
DEFAULT_ENV = env_names(HOST, PORT, USERNAME, PASSWORD, FOLDERS, AUTHSERV_ID)

# A listing fetches each message's metadata and the start of its text, so allow for a page.
LIST_TIMEOUT_SECONDS = 120

_PRINTABLE = r"^[ -~]*$"
"""A Gmail query is sent as an IMAP quoted string, which holds printable ASCII only."""
_SEARCH_TEXT = r"^[^\x00-\x1f\x7f]*$"
"""Header and body search text: any text without control characters. ASCII is sent as a quoted
string, anything else as a UTF-8 literal with ``CHARSET UTF-8``."""


@dataclass(frozen=True)
class ImapClient:
    """A toolkit's client: the mailbox reader the tools share."""

    transport: Any
    """An :class:`ImapMailboxClient`."""

    async def aclose(self) -> None:
        await self.transport.aclose()


def imap_toolkit(
    name: str = "imap",
    *,
    host: str = HOST,
    port: str | None = PORT,
    username: str = USERNAME,
    password: str = PASSWORD,
    folders: str | None = FOLDERS,
    authserv_id: str | None = AUTHSERV_ID,
    artifact_sink: ArtifactSink | None = None,
    attachment_policy: AttachmentPolicy | None = None,
    documents: DocumentReader | None = None,
    imap_factory: ImapFactory | None = None,
    client: Any = None,
) -> Toolkit:
    """A read-only toolkit for one IMAP mailbox: ``<name>.list_messages``,
    ``<name>.list_folders``, ``<name>.get_message``, ``<name>.get_thread`` and, with an
    ``artifact_sink``, ``<name>.get_attachment`` (all read).
    With ``documents`` (a :class:`~algen_agent_tools.documents.DocumentReader`),
    ``<name>.read_attachment`` (read) returns an attachment's inspected text in one call, without
    the file reaching the model; with an ``artifact_sink`` too, the file is also saved.

    Every setting is an ``env://`` reference resolved when the client opens. ``host``,
    ``username`` and ``password`` (an app password) are required; ``port`` defaults to 993 and
    the connection is always TLS. ``folders`` is a comma-separated list of the folders tools may
    read (default ``INBOX``); a special-use name such as ``\\Sent`` or ``\\All`` stands for the
    server's folder with that flag. ``authserv_id`` names the server whose Authentication-Results
    header ``sender_auth`` trusts (default: Gmail's on a Gmail server, otherwise none).
    ``imap_factory`` replaces the connection, for tests.
    """
    references = (host, port, username, password, folders, authserv_id)

    async def open_client(secrets: SecretResolver) -> ImapClient:
        port_value = await resolve_optional(secrets, port) or "993"
        if not port_value.isdigit():
            raise ConfigurationError("IMAP port must be a number")
        try:
            settings = ImapSettings(
                host=await secrets(host),
                port=int(port_value),
                username=await secrets(username),
                password=SecretStr(await secrets(password)),
                folders=split_list(await resolve_optional(secrets, folders)) or ("INBOX",),
                authserv_id=await resolve_optional(secrets, authserv_id),
            )
        except ValidationError as exc:
            # The messages only: a ValidationError would echo the values it was given.
            problems = "; ".join(str(error["msg"]) for error in exc.errors())
            raise ConfigurationError(f"IMAP settings are invalid: {problems}") from None
        return ImapClient(ImapMailboxClient(settings, imap_factory=imap_factory))

    toolkit = Toolkit(name, env=env_names(*references), client=client or open_client)

    @toolkit.tool(read(timeout=LIST_TIMEOUT_SECONDS), audit_metadata=AUDIT_METADATA)
    async def list_messages(
        folder: Annotated[str, Field(min_length=1, max_length=200)] = "INBOX",
        from_address: Annotated[str | None, Field(max_length=320, pattern=_SEARCH_TEXT)] = None,
        subject: Annotated[str | None, Field(max_length=200, pattern=_SEARCH_TEXT)] = None,
        since: date | None = None,
        before: date | None = None,
        unread_only: bool = False,
        text_contains: Annotated[str | None, Field(max_length=200, pattern=_SEARCH_TEXT)] = None,
        gmail_query: Annotated[str | None, Field(max_length=500, pattern=_PRINTABLE)] = None,
        max_results: Annotated[int, Field(ge=1, le=100)] = 25,
        page_token: Annotated[Identifier | None, Field(max_length=32)] = None,
    ) -> ImapMailboxPage:
        """List messages in one mailbox folder, newest first: sender, recipients, subject, a
        short snippet, flags and attachments. Every filter given must match: from_address and
        subject are text the From and Subject headers contain, since and before are dates
        (YYYY-MM-DD; since includes the day, before excludes it), unread_only keeps unseen
        mail, text_contains is text anywhere in the headers or body, and gmail_query is a Gmail
        search such as "has:attachment newer_than:7d" (ASCII; Gmail servers only). Text that
        is not ASCII needs a server that searches UTF-8; one that does not refuses it. folder is a name from list_folders or a special-use name such as \\Sent. Reading
        never marks a message as seen. Use next_page_token to continue."""
        mailbox: ImapClient = await toolkit.client()
        page: ImapMailboxPage = await mailbox.transport.list_messages(
            folder=folder,
            from_address=from_address,
            subject=subject,
            since=since,
            before=before,
            unread_only=unread_only,
            text_contains=text_contains,
            gmail_query=gmail_query,
            max_results=max_results,
            page_token=page_token,
        )
        return page

    @toolkit.tool(read(timeout=60), audit_metadata=AUDIT_METADATA)
    async def list_folders() -> ImapFolderList:
        """List the folders this mailbox's tools may read, with each one's special use (\\Sent,
        \\All, \\Archive, \\Drafts, \\Junk, \\Trash) when the server marks it."""
        mailbox: ImapClient = await toolkit.client()
        folders: ImapFolderList = await mailbox.transport.list_folders()
        return folders

    add_mailbox_read_tools(
        toolkit,
        provider="IMAP",
        artifact_sink=artifact_sink,
        attachment_policy=attachment_policy,
        documents=documents,
    )
    return toolkit
