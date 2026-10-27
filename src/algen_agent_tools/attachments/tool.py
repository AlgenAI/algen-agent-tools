"""The ``get_attachment`` action every connector that receives files shares."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from algen_agent_runtime.tools.authoring import Toolkit, read
from algen_agent_runtime.tools.contracts import Tool, ToolContext
from pydantic import Field

from algen_agent_tools.attachments.models import ArtifactSink, AttachmentRef, AttachmentUpload
from algen_agent_tools.attachments.policy import AttachmentPolicy

Fetch = Callable[[Any, str, str], Awaitable[AttachmentUpload]]
"""Downloads one attachment: ``(client, message_id, attachment_id)``. The client is the
toolkit's own, so the fetch reaches only the connector's configured hosts."""

ID_PATTERN = r"^[A-Za-z0-9_=-][A-Za-z0-9_.=-]*$"
"""Provider ids as tools accept them: no slashes, and never ``.`` or ``..``, so an id cannot
change which API path a connector calls."""

ATTACHMENT_ID_DESCRIPTION = (
    "The attachment's id from the message: its part_id when the listing gives one, otherwise "
    "its provider_attachment_id."
)


def id_field(pattern: str = ID_PATTERN, *, max_length: int = 2_000) -> Any:
    """A tool input type for a provider id that must match ``pattern``. A pattern may allow more
    characters than :data:`ID_PATTERN` (Microsoft Graph ids contain ``/`` and ``+``) as long as
    it never matches ``.`` or ``..`` and the client encodes the id into one path segment."""
    return Annotated[str, Field(min_length=1, max_length=max_length, pattern=pattern)]


def add_get_attachment_tool(
    toolkit: Toolkit,
    *,
    provider: str,
    fetch: Fetch,
    sink: ArtifactSink,
    policy: AttachmentPolicy,
    timeout: float = 120,
    audit_metadata: dict[str, str] | None = None,
    id_pattern: str = ID_PATTERN,
) -> Tool:
    """Add ``<toolkit>.get_attachment``: fetch one attachment, check it against ``policy``,
    store it through ``sink`` and return the artifact reference. File bytes never reach the
    model."""

    async def get_attachment(
        message_id: str, attachment_id: str, context: ToolContext
    ) -> AttachmentRef:
        client = await toolkit.client()
        upload = await fetch(client, message_id, attachment_id)
        policy.check(upload)
        return await sink(upload, context)

    identifier = id_field(id_pattern)
    get_attachment.__annotations__ = {
        "message_id": identifier,
        "attachment_id": Annotated[identifier, Field(description=ATTACHMENT_ID_DESCRIPTION)],
        "context": ToolContext,
        "return": AttachmentRef,
    }
    get_attachment.__doc__ = (
        f"Save one attachment of a {provider} message as an artifact and return its reference "
        "(artifact id, file name and type). Use the ids from a message listing; pass the "
        "reference to a tool that reads documents to see the content."
    )
    return toolkit.tool(
        read(timeout=timeout),
        max_result_bytes=16_000,
        audit_metadata=audit_metadata or {"content_capture": "disabled"},
    )(get_attachment)
