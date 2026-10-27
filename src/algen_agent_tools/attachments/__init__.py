"""Attachments shared by every connector that sends or receives files: artifact references,
the deployment's loader and sink, a size and type policy, and the ``get_attachment`` tool."""

from algen_agent_tools.attachments.models import (
    ArtifactLoader,
    ArtifactSink,
    AttachmentRef,
    AttachmentUpload,
)
from algen_agent_tools.attachments.policy import SIGNATURES, AttachmentPolicy, AttachmentRefused
from algen_agent_tools.attachments.tool import Fetch, add_get_attachment_tool

__all__ = [
    "SIGNATURES",
    "ArtifactLoader",
    "ArtifactSink",
    "AttachmentPolicy",
    "AttachmentRef",
    "AttachmentRefused",
    "AttachmentUpload",
    "Fetch",
    "add_get_attachment_tool",
]
