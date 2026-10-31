"""Read the text of documents agents receive, through document security checks.

The toolkit needs an artifact loader: the deployment's, or ``runtime_attachment_loader`` for the
files attached to the run. It is built in code (there is no zero-configuration plugin yet).

The document inspection itself (``extractor_for`` and its contracts) lives in
:mod:`algen_agent_tools.documents.inspection` and is re-exported here.
"""

from algen_agent_tools.documents.inspection import (
    DocumentDisposition,
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    FindingSeverity,
    extractor_for,
)
from algen_agent_tools.documents.toolkit import (
    DocumentFinding,
    DocumentRefused,
    DocumentText,
    documents_toolkit,
)

__all__ = [
    "DocumentDisposition",
    "DocumentFinding",
    "DocumentRefused",
    "DocumentSecurityFinding",
    "DocumentSpan",
    "DocumentText",
    "ExtractedDocument",
    "FindingSeverity",
    "documents_toolkit",
    "extractor_for",
]
