"""What the Microsoft Graph connectors (Outlook, OneDrive and SharePoint files) share: app
credentials (a client secret or a certificate) and Graph's throttling signal."""

from algen_agent_tools.msgraph.credentials import (
    CLIENT_ASSERTION_TYPE,
    MAX_ASSERTION_SECONDS,
    CertificateInfo,
    CertificateSigner,
    ClientAuthentication,
    GraphCertificate,
    certificate_info,
    check_one_credential,
    client_authentication,
    normalize_thumbprint,
    unescape_pem,
)
from algen_agent_tools.msgraph.throttling import (
    DEFAULT_RETRY_AFTER_SECONDS,
    GraphThrottled,
    retry_after_seconds,
)

__all__ = [
    "CLIENT_ASSERTION_TYPE",
    "DEFAULT_RETRY_AFTER_SECONDS",
    "MAX_ASSERTION_SECONDS",
    "CertificateInfo",
    "CertificateSigner",
    "ClientAuthentication",
    "GraphCertificate",
    "GraphThrottled",
    "certificate_info",
    "check_one_credential",
    "client_authentication",
    "normalize_thumbprint",
    "retry_after_seconds",
    "unescape_pem",
]
