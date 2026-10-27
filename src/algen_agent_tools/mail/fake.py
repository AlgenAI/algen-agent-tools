"""An in-memory mailbox for offline tests of the SMTP and Gmail toolkits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from algen_agent_tools._env import SecretResolver
from algen_agent_tools.mail.models import (
    EmailAddress,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    EmailMessage,
)
from algen_agent_tools.mail.tool import MailClient


@dataclass
class FakeMailbox:
    """Records sent messages and serves an inbox.

    A repeated idempotency key returns the first receipt without sending again, as the real
    transports' deterministic Message-ID does for a receiving server.
    """

    inbox: list[Any] = field(default_factory=list)
    """``InboundEmailSummary`` rows that ``list_messages`` pages through."""
    sent: list[EmailMessage] = field(default_factory=list)
    receipts: dict[str, EmailDeliveryReceipt] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    closed: bool = False

    def factory(
        self, sender: str = "agent@example.com", *, allowed_domains: tuple[str, ...] = ()
    ) -> Any:
        """A toolkit ``client`` that sends as ``sender`` through this mailbox, with
        ``allowed_domains`` as if it came from the deployment's settings."""

        async def open_client(secrets: SecretResolver) -> MailClient:
            return MailClient(EmailAddress(address=sender), self, allowed_domains)

        return open_client

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailDeliveryReceipt:
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        self.sent.append(message)
        receipt = EmailDeliveryReceipt(
            provider="fake",
            provider_message_id=f"fake-{len(self.sent)}",
            status=EmailDeliveryStatus.ACCEPTED,
            accepted_recipients=tuple(
                item.address for item in (*message.to, *message.cc, *message.bcc)
            ),
        )
        self.receipts[idempotency_key] = receipt
        return receipt

    async def list_messages(
        self, *, query: str = "is:unread", max_results: int = 25, page_token: str | None = None
    ) -> Any:
        from algen_agent_tools.gmail.client import GmailMailboxPage

        self.queries.append(query)
        start = int(page_token or 0)
        page = self.inbox[start : start + max_results]
        following = start + max_results
        return GmailMailboxPage(
            messages=tuple(page),
            next_page_token=str(following) if following < len(self.inbox) else None,
            result_size_estimate=len(self.inbox),
        )

    async def aclose(self) -> None:
        self.closed = True
