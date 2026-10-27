from __future__ import annotations

from typing import Any, Literal, cast

from algen_agent_runtime.exceptions.errors import ConfigurationError
from algen_agent_runtime.tools.authoring import Toolkit
from pydantic import SecretStr

from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.mail.models import EmailAddress, EmailArtifactLoader
from algen_agent_tools.mail.tool import (
    ContextualArtifactLoader,
    MailClient,
    add_send_tool,
    bind_artifact_loader,
)
from algen_agent_tools.smtp.sender import SmtpEmailSender, SmtpSettings

HOST = "env://ALGEN_TOOLS_SMTP_HOST"
PORT = "env://ALGEN_TOOLS_SMTP_PORT"
SECURITY = "env://ALGEN_TOOLS_SMTP_SECURITY"
USERNAME = "env://ALGEN_TOOLS_SMTP_USERNAME"
PASSWORD = "env://ALGEN_TOOLS_SMTP_PASSWORD"
SENDER = "env://ALGEN_TOOLS_SMTP_SENDER"
DEFAULT_ENV = env_names(HOST, PORT, SECURITY, USERNAME, PASSWORD, SENDER)


def smtp_toolkit(
    name: str = "smtp",
    *,
    host: str = HOST,
    sender: str = SENDER,
    port: str | None = PORT,
    security: str | None = SECURITY,
    username: str | None = USERNAME,
    password: str | None = PASSWORD,
    allowed_domains: tuple[str, ...] = (),
    artifact_loader: ContextualArtifactLoader | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit with ``<name>.send_email`` over SMTP.

    Every setting is an ``env://`` reference resolved when the client opens. ``host`` and
    ``sender`` are required; ``port`` (default 587), ``security`` (``starttls``, ``tls`` or
    ``plain``; default ``starttls``), ``username`` and ``password`` may be unset. With an
    ``artifact_loader`` the tool can attach Runtime artifacts by reference.
    """
    references = (host, sender, port, security, username, password)

    async def open_client(secrets: SecretResolver) -> MailClient:
        login = await resolve_optional(secrets, username)
        secret = await resolve_optional(secrets, password)
        mode = await resolve_optional(secrets, security) or "starttls"
        if mode not in ("starttls", "tls", "plain"):
            raise ConfigurationError(f"SMTP security must be starttls, tls or plain, not {mode!r}")
        port_value = await resolve_optional(secrets, port) or "587"
        if not port_value.isdigit():
            raise ConfigurationError("SMTP port must be a number")
        settings = SmtpSettings(
            host=await secrets(host),
            port=int(port_value),
            security=cast(Literal["starttls", "tls", "plain"], mode),
            username=login,
            password=SecretStr(secret) if secret is not None else None,
        )
        return MailClient(
            EmailAddress(address=await secrets(sender)),
            SmtpEmailSender(settings, artifact_loader=_bound(artifact_loader)),
        )

    toolkit = Toolkit(name, env=env_names(*references), client=client or open_client)
    add_send_tool(
        toolkit,
        provider="SMTP",
        attachments=artifact_loader is not None,
        allowed_domains=allowed_domains,
    )
    return toolkit


def _bound(loader: ContextualArtifactLoader | None) -> EmailArtifactLoader | None:
    return bind_artifact_loader(loader) if loader is not None else None
