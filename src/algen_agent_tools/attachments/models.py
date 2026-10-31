"""Files move between connectors and agents by reference to a Runtime artifact, never as bytes
in a tool's arguments or result."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from algen_agent_runtime.tools.contracts import ToolContext
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _single_line(value: str, field: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} cannot contain line breaks")


def check_media_type(media_type: str, field: str) -> None:
    """Refuse anything but a bare ``type/subtype``."""
    kind, separator, subtype = media_type.partition("/")
    if not separator or not kind or not subtype:
        raise ValueError(f"{field} must be a type/subtype value")
    if any(character.isspace() for character in media_type) or any(
        character in media_type for character in ";\r\n"
    ):
        raise ValueError(f"{field} must not contain parameters or whitespace")


class AttachmentRef(BaseModel):
    """Tenant-scoped Runtime artifact reference; message contracts never embed file bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str = Field(min_length=1, max_length=256)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def validate_filename(self) -> AttachmentRef:
        _single_line(self.filename, "attachment filename")
        if "/" in self.filename or "\\" in self.filename or self.filename in {".", ".."}:
            raise ValueError("attachment filename must not contain a path")
        check_media_type(self.media_type, "attachment media_type")
        return self


@dataclass(frozen=True)
class AttachmentUpload:
    """A file a connector fetched, on its way into the deployment's artifact store."""

    filename: str
    media_type: str
    data: bytes


ArtifactLoader = Callable[[AttachmentRef, ToolContext], Awaitable[bytes]]
"""Reads an artifact's bytes for one tool call. It receives the call's context so it can check
that the artifact belongs to the caller's tenant (and run) before returning anything."""

ArtifactSink = Callable[[AttachmentUpload, ToolContext], Awaitable[AttachmentRef]]
"""Stores a fetched file as an artifact of the calling tenant and returns its reference. The
deployment provides it; it may scan the file before accepting it."""


async def runtime_attachment_loader(reference: AttachmentRef, context: ToolContext) -> bytes:
    """An :data:`ArtifactLoader` over the Runtime's own artifact store, for the files attached
    to the run (``RunRequest.attachments``, Runtime 0.1.0a19).

    It reads only the run's attachments, for the run's tenant: any other artifact is refused
    (``PolicyDeniedError``), so a model cannot name an arbitrary artifact id. Use the
    deployment's loader for files that reach the agent another way (an email attachment saved
    with ``gmail.get_attachment``, say).
    """
    artifact = await context.read_attachment(reference.artifact_id)
    return artifact.data
