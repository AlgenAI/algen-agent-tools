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

_Id = Annotated[str, Field(min_length=1, max_length=2_000)]


def add_get_attachment_tool(
    toolkit: Toolkit,
    *,
    provider: str,
    fetch: Fetch,
    sink: ArtifactSink,
    policy: AttachmentPolicy,
    timeout: float = 120,
    audit_metadata: dict[str, str] | None = None,
) -> Tool:
    """Add ``<toolkit>.get_attachment``: fetch one attachment, check it against ``policy``,
    store it through ``sink`` and return the artifact reference. File bytes never reach the
    model."""

    async def get_attachment(
        message_id: _Id, attachment_id: _Id, context: ToolContext
    ) -> AttachmentRef:
        client = await toolkit.client()
        upload = await fetch(client, message_id, attachment_id)
        policy.check(upload)
        return await sink(upload, context)

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
