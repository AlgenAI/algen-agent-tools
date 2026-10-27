"""What a connector accepts before a fetched file reaches the artifact store."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from algen_agent_tools.attachments.models import AttachmentUpload, check_media_type

_ZIP = (b"PK\x03\x04",)
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "application/zip": _ZIP,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _ZIP,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _ZIP,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": _ZIP,
}
"""Leading bytes each media type must start with; a file claiming one of these types and not
starting with its signature is refused."""

_TEXT = re.compile(r"^text/|^application/(json|xml)$")
_SNIFF_BYTES = 8_192


class AttachmentRefused(ValueError):
    """A fetched file breaks the connector's attachment policy. The message names the rule, not
    the file's content."""


class AttachmentPolicy(BaseModel):
    """Size and type limits for files a connector fetches.

    ``media_types`` empty accepts any well-formed type. The declared type must match the file's
    leading bytes for the types in :data:`SIGNATURES`, and text types must not contain NUL
    bytes, so a renamed executable is not stored as a PDF or a text file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)
    media_types: tuple[str, ...] = ()

    def check(self, upload: AttachmentUpload) -> None:
        if not upload.data:
            raise AttachmentRefused("attachment is empty")
        if len(upload.data) > self.max_bytes:
            raise AttachmentRefused("attachment exceeds the configured size limit")
        media_type = upload.media_type.casefold()
        try:
            check_media_type(media_type, "attachment media_type")
        except ValueError as exc:
            raise AttachmentRefused(str(exc)) from None
        if self.media_types and media_type not in self.media_types:
            raise AttachmentRefused(f"attachment type {media_type} is not allowed")
        signatures = SIGNATURES.get(media_type)
        if signatures and not upload.data.startswith(signatures):
            raise AttachmentRefused(f"attachment content does not match its type {media_type}")
        if _TEXT.match(media_type) and b"\x00" in upload.data[:_SNIFF_BYTES]:
            raise AttachmentRefused(f"attachment content does not match its type {media_type}")
