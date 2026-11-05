"""Unread content is reported as coverage, separately from security findings (#40)."""

from __future__ import annotations

from algen_agent_tools.documents.inspection import (
    DocumentDisposition,
    DocumentSecurityFinding,
    ExtractedDocument,
    FindingSeverity,
    UnreadContent,
    classify_findings,
)


def finding(
    kind: str, severity: FindingSeverity, page: int | None = None
) -> DocumentSecurityFinding:
    return DocumentSecurityFinding(kind=kind, severity=severity, message="m", page=page)


def test_the_category_follows_the_kind_unless_given() -> None:
    assert finding("document.ocr_required", FindingSeverity.MEDIUM).category == "coverage"
    assert finding("document.macros", FindingSeverity.MEDIUM).category == "security"
    explicit = DocumentSecurityFinding(
        kind="vendor.unread_attachment",
        severity=FindingSeverity.LOW,
        message="m",
        category="coverage",
    )
    assert explicit.category == "coverage"


def test_only_security_findings_set_the_disposition() -> None:
    unread = [finding("document.ocr_required", FindingSeverity.MEDIUM)]
    assert classify_findings(unread)[1] == DocumentDisposition.PASS

    macros = [*unread, finding("document.macros", FindingSeverity.MEDIUM)]
    assert classify_findings(macros)[1] == DocumentDisposition.REVIEW

    injection = [
        *unread,
        finding("prompt_injection.instruction_override", FindingSeverity.CRITICAL),
    ]
    assert classify_findings(injection)[1] == DocumentDisposition.QUARANTINE


def test_coverage_is_derived_from_the_findings_and_the_text() -> None:
    def document(text: str, *findings: DocumentSecurityFinding) -> ExtractedDocument:
        return ExtractedDocument(
            media_type="application/pdf",
            text=text,
            sanitized_text=text,
            spans=(),
            findings=findings,
        )

    assert document("All read").coverage == "full"
    scan = document("", finding("document.ocr_required", FindingSeverity.MEDIUM))
    assert (scan.coverage, scan.needs_ocr, scan.unread) == (
        "none",
        True,
        (UnreadContent(kind="image"),),
    )
    sheet = document("rows", finding("document.truncated", FindingSeverity.MEDIUM, page=2))
    assert (sheet.coverage, sheet.unread) == ("partial", (UnreadContent(kind="rows", page=2),))
    # A security finding alone says nothing about coverage.
    assert document("text", finding("document.macros", FindingSeverity.MEDIUM)).coverage == "full"


def test_an_extracted_document_round_trips() -> None:
    scan = ExtractedDocument(
        media_type="application/pdf",
        text="",
        sanitized_text="",
        spans=(),
        findings=(finding("document.ocr_required", FindingSeverity.MEDIUM, page=3),),
    )
    assert ExtractedDocument.model_validate(scan.model_dump()) == scan
    assert ExtractedDocument.model_validate_json(scan.model_dump_json()) == scan
