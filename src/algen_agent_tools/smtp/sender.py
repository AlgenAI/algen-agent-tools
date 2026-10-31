"""SMTP delivery with a deterministic Message-ID and bounded artifact attachments.

Moved from ``algen_agent_runtime.communications.email`` (Runtime 0.1.0a11).
"""

from __future__ import annotations

import asyncio
import smtplib
import socket
import ssl
from collections.abc import Callable, Mapping
from email.message import EmailMessage as MimeEmailMessage
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.errors import not_attempted
from algen_agent_tools.mail.models import (
    DOMAIN,
    EmailArtifactLoader,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    EmailMessage,
    build_mime_message,
    reject_header_injection,
)

# Failures that leave the message undelivered: the server could not be reached, refused the
# login, the sender, every recipient or the message itself. A timeout or a dropped connection
# may come after the server accepted the message, so those stay unmarked.
_REFUSED_BEFORE_DELIVERY = (
    ConnectionRefusedError,
    socket.gaierror,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPNotSupportedError,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPDataError,
)


class SmtpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls", "plain"] = "starttls"
    username: str | None = Field(default=None, max_length=320)
    password: SecretStr | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    local_hostname: str | None = Field(default=None, max_length=253)
    allow_insecure_plaintext: bool = False
    message_id_domain: str = Field(default="algen.local", min_length=1, max_length=253)
    max_attachment_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)

    @model_validator(mode="after")
    def validate_transport(self) -> SmtpSettings:
        reject_header_injection(self.host, "SMTP host")
        reject_header_injection(self.message_id_domain, "message ID domain")
        if not DOMAIN.fullmatch(self.message_id_domain):
            raise ValueError("message_id_domain is invalid")
        if (self.username is None) != (self.password is None):
            raise ValueError("SMTP username and password must be configured together")
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if (
            self.security == "plain"
            and self.host.casefold() not in local_hosts
            and not self.allow_insecure_plaintext
        ):
            raise ValueError("plaintext SMTP is restricted to loopback unless explicitly enabled")
        return self


SmtpFactory = Callable[[SmtpSettings], Any]


class SmtpEmailSender:
    """SMTP adapter with deterministic Message-ID and bounded artifact attachments."""

    def __init__(
        self,
        settings: SmtpSettings,
        *,
        artifact_loader: EmailArtifactLoader | None = None,
        smtp_factory: SmtpFactory | None = None,
    ) -> None:
        self._settings = settings
        self._artifact_loader = artifact_loader
        self._smtp_factory = smtp_factory or self._open_client

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailDeliveryReceipt:
        attachments: list[tuple[EmailAttachmentReference, bytes]] = []
        total_bytes = 0
        for reference in message.attachments:
            if self._artifact_loader is None:
                raise not_attempted(
                    EmailDeliveryError("SMTP attachments require an artifact loader")
                )
            payload = await self._artifact_loader(reference)
            total_bytes += len(payload)
            if total_bytes > self._settings.max_attachment_bytes:
                raise not_attempted(
                    EmailDeliveryError("email attachments exceed the configured size limit")
                )
            attachments.append((reference, payload))
        mime, message_id = self._build_message(message, idempotency_key, attachments)
        all_recipients = tuple(item.address for item in (*message.to, *message.cc, *message.bcc))
        try:
            refused = await asyncio.to_thread(self._send_sync, mime, all_recipients)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            error = EmailDeliveryError(f"SMTP delivery failed ({type(exc).__name__})")
            if isinstance(exc, _REFUSED_BEFORE_DELIVERY):
                raise not_attempted(error) from exc
            raise error from exc
        rejected = {address: f"SMTP {details[0]}" for address, details in refused.items()}
        accepted = tuple(address for address in all_recipients if address not in refused)
        return EmailDeliveryReceipt(
            provider="smtp",
            provider_message_id=message_id,
            status=(EmailDeliveryStatus.ACCEPTED if accepted else EmailDeliveryStatus.REJECTED),
            accepted_recipients=accepted,
            rejected_recipients=rejected,
        )

    def _build_message(
        self,
        message: EmailMessage,
        idempotency_key: str,
        attachments: list[tuple[EmailAttachmentReference, bytes]],
    ) -> tuple[MimeEmailMessage, str]:
        return build_mime_message(
            message,
            idempotency_key=idempotency_key,
            attachments=attachments,
            message_id_domain=self._settings.message_id_domain,
        )

    def _open_client(self, settings: SmtpSettings) -> Any:
        if settings.security == "tls":
            return smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                local_hostname=settings.local_hostname,
                timeout=settings.timeout_seconds,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(
            settings.host,
            settings.port,
            local_hostname=settings.local_hostname,
            timeout=settings.timeout_seconds,
        )

    def _send_sync(
        self, message: MimeEmailMessage, recipients: tuple[str, ...]
    ) -> Mapping[str, tuple[int, bytes]]:
        client = self._smtp_factory(self._settings)
        try:
            client.ehlo()
            if self._settings.security == "starttls":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self._settings.username is not None and self._settings.password is not None:
                client.login(self._settings.username, self._settings.password.get_secret_value())
            return cast(
                Mapping[str, tuple[int, bytes]],
                client.send_message(message, to_addrs=recipients),
            )
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                client.close()
