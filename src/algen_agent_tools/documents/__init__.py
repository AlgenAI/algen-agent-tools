"""Read the text of documents agents receive, through the Runtime's document security checks.

The toolkit needs an artifact loader from the deployment, so it is built in code (there is no
zero-configuration plugin yet).
"""

from algen_agent_tools.documents.toolkit import (
    DocumentFinding,
    DocumentRefused,
    DocumentText,
    documents_toolkit,
)

__all__ = ["DocumentFinding", "DocumentRefused", "DocumentText", "documents_toolkit"]
