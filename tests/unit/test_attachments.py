"""Attachments: artifact references, the size and type policy and the shared get_attachment
tool."""

from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.tools.authoring import Toolkit
from algen_agent_runtime.tools.contracts import SideEffect, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import ValidationError

from algen_agent_tools.attachments import (
    AttachmentPolicy,
    AttachmentRef,
    AttachmentRefused,
    AttachmentUpload,
    add_get_attachment_tool,
)
from algen_agent_tools.mail import EmailAttachmentReference
from algen_agent_tools.testing import assert_toolkit_contract

PDF = b"%PDF-1.7\n..."


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"filename": "../secret.pdf"}, "path"),
        ({"filename": "a\r\nBcc: x"}, "line breaks"),
        ({"media_type": "pdf"}, "type/subtype"),
        ({"media_type": "text/plain; charset=utf-8"}, "parameters"),
    ],
)
def test_references_refuse_paths_header_breaks_and_loose_types(
    fields: dict[str, str], message: str
) -> None:
    valid = {"artifact_id": "a1", "filename": "report.pdf", "media_type": "application/pdf"}
    with pytest.raises(ValidationError, match=message):
        AttachmentRef(**{**valid, **fields})


def test_the_email_reference_is_an_attachment_reference_with_its_own_schema_name() -> None:
    reference = EmailAttachmentReference(
        artifact_id="a1", filename="report.pdf", media_type="application/pdf"
    )
    assert isinstance(reference, AttachmentRef)
    assert EmailAttachmentReference.model_json_schema()["title"] == "EmailAttachmentReference"


@pytest.mark.parametrize(
    ("upload", "policy", "message"),
    [
        (AttachmentUpload("a.pdf", "application/pdf", b""), AttachmentPolicy(), "empty"),
        (AttachmentUpload("a.pdf", "application/pdf", PDF), AttachmentPolicy(max_bytes=4), "size"),
        (
            AttachmentUpload("a.pdf", "application/pdf", PDF),
            AttachmentPolicy(media_types=("image/png",)),
            "not allowed",
        ),
        (AttachmentUpload("a.pdf", "application/pdf", b"MZ\x90\x00"), AttachmentPolicy(), "match"),
        (AttachmentUpload("a.txt", "text/plain", b"MZ\x00\x00"), AttachmentPolicy(), "match"),
        (AttachmentUpload("a", "application/x; y=z", b"x"), AttachmentPolicy(), "parameters"),
    ],
)
def test_the_policy_refuses_by_rule(
    upload: AttachmentUpload, policy: AttachmentPolicy, message: str
) -> None:
    with pytest.raises(AttachmentRefused, match=message):
        policy.check(upload)


@pytest.mark.parametrize(
    "upload",
    [
        AttachmentUpload("a.pdf", "application/pdf", PDF),
        AttachmentUpload("a.pdf", "Application/PDF", PDF),
        AttachmentUpload("notes.md", "text/markdown", b"# Notes"),
        AttachmentUpload("blob.bin", "application/octet-stream", b"\x00\x01"),
    ],
)
def test_the_policy_accepts_matching_files(upload: AttachmentUpload) -> None:
    AttachmentPolicy().check(upload)


class FakeInbox:
    def __init__(self) -> None:
        self.files = {
            ("m1", "a1"): AttachmentUpload("report.pdf", "application/pdf", PDF),
            ("m1", "bad"): AttachmentUpload("x.pdf", "application/pdf", b"MZ"),
        }


async def fetch(client: Any, message_id: str, attachment_id: str) -> AttachmentUpload:
    return client.files[(message_id, attachment_id)]  # type: ignore[no-any-return]


def toolkit_with_sink(
    inbox: FakeInbox, stored: list[tuple[AttachmentUpload, ToolContext]]
) -> Toolkit:
    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        stored.append((upload, context))
        return AttachmentRef(
            artifact_id=f"art-{len(stored)}", filename=upload.filename, media_type=upload.media_type
        )

    toolkit = Toolkit("inbox", client=lambda secrets: inbox)
    add_get_attachment_tool(
        toolkit, provider="Inbox", fetch=fetch, sink=sink, policy=AttachmentPolicy()
    )
    return toolkit


async def test_get_attachment_stores_the_file_and_returns_only_its_reference() -> None:
    stored: list[tuple[AttachmentUpload, ToolContext]] = []
    inbox = FakeInbox()
    toolkit = toolkit_with_sink(inbox, stored)
    assert_toolkit_contract(toolkit)
    [tool] = toolkit.tools
    assert tool.definition.name == "inbox.get_attachment"
    assert tool.definition.side_effect is SideEffect.READ
    assert tool.definition.required_permissions == frozenset({"inbox.read"})

    context = fake_tool_context(tool)
    result = await tool.execute({"message_id": "m1", "attachment_id": "a1"}, context)
    assert result == {
        "artifact_id": "art-1",
        "filename": "report.pdf",
        "media_type": "application/pdf",
    }
    assert stored == [(inbox.files[("m1", "a1")], context)]


async def test_get_attachment_refuses_before_storing() -> None:
    stored: list[tuple[AttachmentUpload, ToolContext]] = []
    toolkit = toolkit_with_sink(FakeInbox(), stored)
    [tool] = toolkit.tools
    with pytest.raises(AttachmentRefused):
        await tool.execute({"message_id": "m1", "attachment_id": "bad"}, fake_tool_context(tool))
    assert stored == []
