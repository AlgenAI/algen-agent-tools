"""Inspect email files (``.eml``) before producing downstream-safe plain text.

The text is a header block (From, To, Cc, Date, Subject) and the body, followed by each
attachment's text. The body is the plain-text part, or the HTML part read by
:class:`~algen_agent_tools.documents.inspection.html.HtmlDocumentExtractor` when there is no plain
one. Each attachment is read by the extractor for its file name; an attached email
(``message/rfc822``) is read by this extractor, at most ``max_depth`` levels deep. At most
``max_attachments`` attachments are read in the whole message; the rest are reported as coverage
findings, as are attachments that cannot be read. An attachment's findings are the message's, so
a quarantined attachment quarantines the message. Standard library only.
"""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import PurePath
from typing import TYPE_CHECKING

from algen_agent_tools.documents.inspection.core import (
    DocumentPart,
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    FindingSeverity,
    PlainTextDocumentExtractor,
    classify_findings,
    extractor_for,
    normalize_document_text,
)

if TYPE_CHECKING:
    from algen_agent_tools.documents.inspection.legacy import DocumentConverter
    from algen_agent_tools.documents.inspection.ocr import TextRecognizer

HEADERS = ("From", "To", "Cc", "Date", "Subject")
_EMAIL_SUFFIXES = {".eml", ".msg"}


class _Budget:
    """Attachments and characters left for the whole message, nested emails included."""

    def __init__(self, attachments: int, characters: int) -> None:
        self.attachments = attachments
        self.characters = characters


def _unread(kind: str, message: str, part: str) -> DocumentSecurityFinding:
    return DocumentSecurityFinding(
        kind=kind, severity=FindingSeverity.LOW, message=message, part=part
    )


def _filename(part: EmailMessage, index: int) -> str:
    name = PurePath(str(part.get_filename() or "").replace("\\", "/")).name
    name = " ".join(name.split())[:255]
    if name:
        return name
    return (
        f"attachment-{index}.eml"
        if part.get_content_type() == "message/rfc822"
        else (f"attachment-{index}")
    )


def _payload(attachment: EmailMessage) -> bytes:
    payload = attachment.get_payload(decode=True)
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("the attachment is empty")
    return payload


class EmailDocumentExtractor:
    """Bounded ``.eml`` inspection; attachments are read with bounded recursion."""

    def __init__(
        self,
        *,
        max_bytes: int = 10_000_000,
        max_depth: int = 2,
        max_attachments: int = 20,
        max_characters: int = 250_000,
        ocr: TextRecognizer | None = None,
        ocr_max_pages: int = 3,
        converter: DocumentConverter | None = None,
    ) -> None:
        if max_depth < 0 or max_attachments < 0:
            raise ValueError("max_depth and max_attachments must be 0 or more")
        self._max_bytes = max_bytes
        self._max_depth = max_depth
        self._max_attachments = max_attachments
        self._max_characters = max_characters
        self._ocr = ocr
        self._ocr_max_pages = ocr_max_pages
        self._converter = converter

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        del filename
        budget = _Budget(self._max_attachments, self._max_characters)
        return self._extract(data, depth=0, budget=budget)

    def _extract(self, data: bytes, *, depth: int, budget: _Budget) -> ExtractedDocument:
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        message = BytesParser(policy=policy.default).parsebytes(data)
        if not isinstance(message, EmailMessage) or not any(key in message for key in HEADERS):
            raise ValueError("file content is not an email message")
        header_lines = [
            f"{key}: {' '.join(str(message[key]).split())}" for key in HEADERS if key in message
        ]
        body = self._body(message)
        unread_body = body is None and "X-Algen-Unread-Body" in message
        head = PlainTextDocumentExtractor().extract(
            "\n".join(header_lines).encode(), filename="headers.txt"
        )
        findings = [*head.findings, *(body.findings if body else ())]
        if unread_body:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.body_unread",
                    severity=FindingSeverity.LOW,
                    message="The message body is RTF only and was not read.",
                )
            )
        texts = [head.text, body.text if body else ""]
        sanitized = [head.sanitized_text, body.sanitized_text if body else ""]
        spans: list[DocumentSpan] = [*head.spans, *(body.spans if body else ())]
        budget.characters -= sum(len(text) for text in texts)
        if budget.characters < 0:
            raise ValueError(f"email exceeds {self._max_characters} extracted character limit")
        parts: list[DocumentPart] = []
        image_count = body.image_count if body else 0
        for index, attachment in enumerate(message.iter_attachments(), start=1):
            name = _filename(attachment, index)
            if budget.attachments <= 0:
                findings.append(
                    _unread(
                        "document.attachment_skipped",
                        "The attachment was not read: the message has more attachments than "
                        "the limit.",
                        name,
                    )
                )
                continue
            budget.attachments -= 1
            child = self._attachment(attachment, name, depth=depth, budget=budget)
            if isinstance(child, DocumentSecurityFinding):
                findings.append(child)
                continue
            if len(child.text) > budget.characters:
                findings.append(
                    _unread(
                        "document.attachment_skipped",
                        "The attachment was not read: the message's text limit was reached.",
                        name,
                    )
                )
                continue
            budget.characters -= len(child.text)
            image_count += child.image_count
            findings.extend(
                finding if finding.part else finding.model_copy(update={"part": name})
                for finding in child.findings
            )
            parts.append(
                DocumentPart(
                    filename=name,
                    media_type=child.media_type,
                    disposition=child.disposition,
                    coverage=child.coverage,
                )
            )
            texts.append(f"[Attachment: {name}]\n{child.text}")
            if child.sanitized_text:
                sanitized.append(f"[Attachment: {name}]\n{child.sanitized_text}")
        unique_findings, disposition = classify_findings(findings)
        return ExtractedDocument(
            media_type="message/rfc822",
            text="\n\n".join(text for text in texts if text),
            sanitized_text="\n\n".join(text for text in sanitized if text),
            spans=tuple(spans),
            findings=unique_findings,
            disposition=disposition,
            image_count=image_count,
            parts=tuple(parts),
        )

    def _body(self, message: EmailMessage) -> ExtractedDocument | None:
        part = message.get_body(preferencelist=("plain", "html"))
        if part is None:
            return None
        try:
            content = part.get_content()
        except LookupError:
            # An unknown charset: read the bytes as UTF-8 rather than refuse the message.
            payload = part.get_payload(decode=True)
            content = (
                payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
            )
        if not isinstance(content, str) or not normalize_document_text(content):
            return None
        if part.get_content_subtype() == "html":
            from algen_agent_tools.documents.inspection.html import HtmlDocumentExtractor

            return HtmlDocumentExtractor().extract(content.encode(), filename="body.html")
        return PlainTextDocumentExtractor().extract(content.encode(), filename="body.txt")

    def _attachment(
        self, attachment: EmailMessage, name: str, *, depth: int, budget: _Budget
    ) -> ExtractedDocument | DocumentSecurityFinding:
        nested = attachment.get_content_type() == "message/rfc822" or (
            PurePath(name).suffix.lower() in _EMAIL_SUFFIXES
        )
        if nested and depth >= self._max_depth:
            return _unread(
                "document.attachment_skipped",
                "The attached email was not read: it is nested deeper than the limit.",
                name,
            )
        try:
            if nested:
                if attachment.get_content_type() == "message/rfc822":
                    content = attachment.get_content()
                    data = (
                        content.as_bytes() if isinstance(content, EmailMessage) else bytes(content)
                    )
                else:
                    data = _payload(attachment)
                    if PurePath(name).suffix.lower() == ".msg":
                        from algen_agent_tools.documents.inspection.msg import msg_to_email

                        data = msg_to_email(data).as_bytes()
                return self._extract(data, depth=depth + 1, budget=budget)
            payload = _payload(attachment)
            extractor = extractor_for(
                name,
                data=payload,
                ocr=self._ocr,
                ocr_max_pages=self._ocr_max_pages,
                converter=self._converter,
            )
            return extractor.extract(payload, filename=name)
        except ImportError as exc:
            extra = {"fitz": "PDF needs the documents extra", "olefile": ".msg needs the msg extra"}
            if exc.name not in extra:
                raise
            return _unread(
                "document.attachment_unread",
                f"The attachment was not read: {extra[exc.name]}.",
                name,
            )
        except (ValueError, LookupError) as exc:
            return _unread(
                "document.attachment_unread",
                f"The attachment was not read: {str(exc)[:200]}.",
                name,
            )
