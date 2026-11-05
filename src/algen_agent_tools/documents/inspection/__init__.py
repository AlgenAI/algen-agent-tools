"""Untrusted document inspection: the text a reader would see, and what was hidden from them.

``extractor_for(filename)`` returns an extractor for PDF, DOCX, XLSX, PPTX, HTML, CSV, TSV, text,
Markdown and email (``.eml``) documents. Each returns an :class:`ExtractedDocument`: the full text, the
``sanitized_text`` a reader would see, per-span evidence, security findings (hidden text,
prompt-injection attempts, macros, remote content, content that was not read) and a
disposition. PDF needs the ``documents`` extra (PyMuPDF); the other formats need nothing more.

``extractor_for(filename, ocr=recognizer)`` also reads images and scanned PDF pages through a
host-supplied :class:`TextRecognizer`; this package ships no OCR engine, and without a recognizer
images are refused.

Moved here from ``algen_agent_runtime.security`` (deprecated there in Runtime 0.1.0a19).
"""

from algen_agent_tools.documents.inspection.core import (
    DocumentDisposition,
    DocumentExtractor,
    DocumentPart,
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    FindingSeverity,
    PdfDocumentExtractor,
    PlainTextDocumentExtractor,
    UnreadContent,
    classify_findings,
    extractor_for,
    prompt_injection_findings,
    redact_pii,
    sanitize_untrusted_text,
)
from algen_agent_tools.documents.inspection.docx import DocxDocumentExtractor
from algen_agent_tools.documents.inspection.email import EmailDocumentExtractor
from algen_agent_tools.documents.inspection.html import HtmlDocumentExtractor
from algen_agent_tools.documents.inspection.image import ImageDocumentExtractor
from algen_agent_tools.documents.inspection.ocr import RecognizedText, TextRecognizer
from algen_agent_tools.documents.inspection.odf import OdfDocumentExtractor
from algen_agent_tools.documents.inspection.pptx import PptxDocumentExtractor
from algen_agent_tools.documents.inspection.tesseract import TesseractRecognizer
from algen_agent_tools.documents.inspection.xlsx import XlsxDocumentExtractor

__all__ = [
    "DocumentDisposition",
    "DocumentExtractor",
    "DocumentPart",
    "DocumentSecurityFinding",
    "DocumentSpan",
    "DocxDocumentExtractor",
    "EmailDocumentExtractor",
    "ExtractedDocument",
    "FindingSeverity",
    "HtmlDocumentExtractor",
    "ImageDocumentExtractor",
    "OdfDocumentExtractor",
    "PdfDocumentExtractor",
    "PlainTextDocumentExtractor",
    "PptxDocumentExtractor",
    "RecognizedText",
    "TesseractRecognizer",
    "TextRecognizer",
    "UnreadContent",
    "XlsxDocumentExtractor",
    "classify_findings",
    "extractor_for",
    "prompt_injection_findings",
    "redact_pii",
    "sanitize_untrusted_text",
]
