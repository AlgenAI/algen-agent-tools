"""App credentials for Microsoft identity platform: a client secret or a certificate.

A certificate credential signs a short-lived client assertion (a JWT, RFC 7523) instead of
sending a shared secret, as Microsoft documents for confidential clients
(https://learn.microsoft.com/entra/identity-platform/certificate-credentials). The key never
leaves the process: only the signed assertion is sent to the token endpoint.

``cryptography`` is imported only when a certificate is used, after the configuration has been
read, so a connector that authenticates with a client secret needs nothing extra.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from algen_agent_runtime.exceptions.errors import ConfigurationError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
MAX_ASSERTION_SECONDS = 600
"""Microsoft accepts an assertion valid for at most ten minutes."""

_HEX = re.compile(r"^[0-9A-F]{40}$")
_PEM_BEGIN = "-----BEGIN "

ClientAuthentication = Callable[[], dict[str, str]]
"""Returns the form fields that authenticate the client in one token request: either
``client_secret``, or ``client_assertion_type`` and a freshly signed ``client_assertion``."""


def unescape_pem(value: str) -> str:
    """A PEM value as it was issued, from one stored on a single line.

    Secret stores and ``.env`` files often hold a PEM with its line breaks written as the two
    characters ``\\n`` (or ``\\r\\n``). A value that already has real line breaks is returned
    unchanged, apart from surrounding whitespace and quotes."""
    text = value.strip().strip('"').strip("'").strip()
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    return text.replace("\r\n", "\n")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def normalize_thumbprint(value: str) -> str:
    """A SHA-1 certificate thumbprint as 40 upper-case hex digits, from the forms Entra,
    PowerShell and OpenSSL print (with or without colons or spaces)."""
    cleaned = re.sub(r"[\s:]", "", value).upper()
    if not _HEX.fullmatch(cleaned):
        raise ValueError("thumbprint must be the certificate's SHA-1 thumbprint (40 hex digits)")
    return cleaned


def _crypto_missing(extra: str) -> ConfigurationError:
    return ConfigurationError(
        "certificate credentials need the 'cryptography' package; install it with "
        f"pip install 'algen-agent-tools[{extra}]'"
    )


@dataclass(frozen=True)
class CertificateInfo:
    """What a host may show about a certificate: never the key."""

    sha1_thumbprint: str
    """Upper-case hex, as the Entra portal shows it."""
    sha256_thumbprint: str
    subject: str
    not_valid_before: datetime
    not_valid_after: datetime

    def expires_within(self, seconds: float, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return (self.not_valid_after - current).total_seconds() <= seconds


def certificate_info(certificate_pem: str, *, extra: str = "outlook") -> CertificateInfo:
    """Thumbprints, subject and validity of a PEM certificate.

    Raises :class:`ValueError` (naming no content) when the value is not a PEM certificate, and
    a ``ConfigurationError`` naming ``extra`` when ``cryptography`` is not installed."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
    except ImportError:
        raise _crypto_missing(extra) from None
    try:
        cert = x509.load_pem_x509_certificate(unescape_pem(certificate_pem).encode())
    except ValueError:
        raise ValueError("certificate is not a PEM X.509 certificate") from None
    der = cert.public_bytes(Encoding.DER)
    return CertificateInfo(
        sha1_thumbprint=hashlib.sha1(der, usedforsecurity=False).hexdigest().upper(),
        sha256_thumbprint=hashlib.sha256(der).hexdigest().upper(),
        subject=cert.subject.rfc4514_string(),
        not_valid_before=cert.not_valid_before_utc,
        not_valid_after=cert.not_valid_after_utc,
    )


class GraphCertificate(BaseModel):
    """A certificate credential for an Entra app registration.

    ``private_key`` is the PEM private key (PKCS#8 or PKCS#1, RSA), encrypted or not;
    ``private_key_passphrase`` decrypts an encrypted one. Identify the certificate with either
    ``certificate`` (the PEM certificate: preferred, the assertion then carries its SHA-256
    thumbprint, ``x5t#S256``) or ``thumbprint`` (its SHA-1 thumbprint as Entra shows it, sent as
    ``x5t``). PEM values may be stored on one line with ``\\n`` escapes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    private_key: SecretStr
    private_key_passphrase: SecretStr | None = None
    certificate: str | None = Field(default=None, max_length=20_000, repr=False)
    thumbprint: str | None = Field(default=None, max_length=200)

    @field_validator("private_key", mode="after")
    @classmethod
    def _key(cls, value: SecretStr) -> SecretStr:
        text = unescape_pem(value.get_secret_value())
        if _PEM_BEGIN not in text or "PRIVATE KEY" not in text:
            raise ValueError("private_key must be a PEM private key")
        return SecretStr(text)

    @field_validator("certificate", mode="after")
    @classmethod
    def _certificate(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        text = unescape_pem(value)
        if "-----BEGIN CERTIFICATE-----" not in text:
            raise ValueError("certificate must be a PEM certificate")
        return text

    @field_validator("thumbprint", mode="after")
    @classmethod
    def _thumbprint(cls, value: str | None) -> str | None:
        return normalize_thumbprint(value) if value and value.strip() else None

    @model_validator(mode="after")
    def _identified(self) -> GraphCertificate:
        if self.certificate is None and self.thumbprint is None:
            raise ValueError("a certificate credential needs the certificate or its thumbprint")
        return self

    def signer(self, *, extra: str = "outlook") -> CertificateSigner:
        """Load the key (and certificate) once; ``extra`` names the package extra to install
        when ``cryptography`` is missing."""
        return CertificateSigner(self, extra=extra)


class CertificateSigner:
    """Signs client assertions with one certificate's private key (RS256)."""

    def __init__(self, credential: GraphCertificate, *, extra: str = "outlook") -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            raise _crypto_missing(extra) from None
        passphrase = credential.private_key_passphrase
        try:
            key = serialization.load_pem_private_key(
                credential.private_key.get_secret_value().encode(),
                password=passphrase.get_secret_value().encode() if passphrase else None,
            )
        except (ValueError, TypeError):
            # The library's message never contains the key, but keep ours fixed anyway.
            raise ConfigurationError(
                "the certificate's private key could not be read (wrong passphrase, or not a "
                "PEM private key)"
            ) from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ConfigurationError("Microsoft Entra certificate credentials must use an RSA key")
        self._key = key
        self.info: CertificateInfo | None = None
        header: dict[str, str] = {"alg": "RS256", "typ": "JWT"}
        if credential.certificate is not None:
            self.info = certificate_info(credential.certificate, extra=extra)
            if not self._matches(credential.certificate):
                raise ConfigurationError("the certificate does not belong to the private key")
            if (
                credential.thumbprint is not None
                and credential.thumbprint != self.info.sha1_thumbprint
            ):
                raise ConfigurationError("the thumbprint does not match the certificate")
            header["x5t#S256"] = _b64url(bytes.fromhex(self.info.sha256_thumbprint))
        else:
            assert credential.thumbprint is not None  # checked by the model
            header["x5t"] = _b64url(bytes.fromhex(credential.thumbprint))
        self._header = header

    def _matches(self, certificate_pem: str) -> bool:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(certificate_pem.encode())
        public = cert.public_key()
        numbers = getattr(public, "public_numbers", None)
        return bool(numbers and numbers() == self._key.public_key().public_numbers())

    @property
    def header(self) -> dict[str, str]:
        return dict(self._header)

    def assertion(
        self,
        *,
        client_id: str,
        audience: str,
        lifetime_seconds: int = MAX_ASSERTION_SECONDS,
        now: float | None = None,
    ) -> str:
        """A signed client assertion for one token request to ``audience`` (the token
        endpoint): ``iss`` and ``sub`` the client id, a fresh ``jti``, valid from now for
        ``lifetime_seconds`` (at most ten minutes)."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not 1 <= lifetime_seconds <= MAX_ASSERTION_SECONDS:
            raise ValueError("an assertion is valid for 1 to 600 seconds")
        issued = int(now if now is not None else time.time())
        claims: dict[str, Any] = {
            "aud": audience,
            "iss": client_id,
            "sub": client_id,
            "jti": str(uuid.uuid4()),
            "nbf": issued,
            "iat": issued,
            "exp": issued + lifetime_seconds,
        }
        signing_input = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode())
            for part in (self._header, claims)
        )
        signature = self._key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64url(signature)}"


def client_authentication(
    *,
    client_id: str,
    token_uri: str,
    client_secret: SecretStr | None = None,
    certificate: GraphCertificate | None = None,
    extra: str = "outlook",
) -> ClientAuthentication:
    """The client authentication for token requests: exactly one of ``client_secret`` and
    ``certificate``. A certificate's key is loaded here, so a wrong key or passphrase fails when
    the client opens, not on its first call."""
    check_one_credential(client_secret, certificate)
    if certificate is not None:
        signer = certificate.signer(extra=extra)

        def assert_client() -> dict[str, str]:
            return {
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": signer.assertion(client_id=client_id, audience=token_uri),
            }

        return assert_client
    assert client_secret is not None
    secret = client_secret

    def send_secret() -> dict[str, str]:
        return {"client_secret": secret.get_secret_value()}

    return send_secret


def check_one_credential(
    client_secret: SecretStr | None, certificate: GraphCertificate | None
) -> None:
    """Exactly one of a client secret and a certificate; raises :class:`ValueError`."""
    has_secret = client_secret is not None and bool(client_secret.get_secret_value())
    if has_secret == (certificate is not None):
        raise ValueError("set exactly one of client_secret and certificate")
