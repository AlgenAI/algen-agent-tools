"""Certificate credentials for Microsoft Graph, Outlook's date window and Graph throttling."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolNotAttemptedError
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.mail import EmailDeliveryError, MailClient
from algen_agent_tools.msgraph import (
    CLIENT_ASSERTION_TYPE,
    GraphCertificate,
    GraphThrottled,
    certificate_info,
    normalize_thumbprint,
    retry_after_seconds,
    unescape_pem,
)
from algen_agent_tools.outlook import OutlookMailboxClient, OutlookSettings, outlook_toolkit
from algen_agent_tools.outlook.client import received_filter

crypto = pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

TOKEN_URI = "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token"


def make_certificate(
    *, days: int = 365, passphrase: bytes | None = None
) -> tuple[str, str, rsa.RSAPrivateKey]:
    """A throwaway self-signed certificate and its PEM private key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Careers-Mailbox-Reader")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()
    return cert.public_bytes(serialization.Encoding.PEM).decode(), key_pem, key


def _part(segment: str) -> Any:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _verify(assertion: str, key: rsa.RSAPrivateKey) -> tuple[dict[str, Any], dict[str, Any]]:
    header, claims, signature = assertion.split(".")
    key.public_key().verify(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return _part(header), _part(claims)


def test_the_assertion_carries_the_sha256_thumbprint_and_verifies_with_the_certificate() -> None:
    cert_pem, key_pem, key = make_certificate()
    signer = GraphCertificate(private_key=SecretStr(key_pem), certificate=cert_pem).signer()
    assertion = signer.assertion(client_id="app-id", audience=TOKEN_URI, now=1_800_000_000)
    header, claims = _verify(assertion, key)

    der = x509.load_pem_x509_certificate(cert_pem.encode()).public_bytes(serialization.Encoding.DER)
    expected = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode()
    assert header == {"alg": "RS256", "typ": "JWT", "x5t#S256": expected}
    assert claims["aud"] == TOKEN_URI
    assert claims["iss"] == claims["sub"] == "app-id"
    assert claims["nbf"] == claims["iat"] == 1_800_000_000
    assert claims["exp"] - claims["iat"] <= 600
    assert len(claims["jti"]) == 36
    again = signer.assertion(client_id="app-id", audience=TOKEN_URI, now=1_800_000_000)
    assert _part(again.split(".")[1])["jti"] != claims["jti"]  # a fresh jti each time


def test_a_thumbprint_alone_sends_the_sha1_x5t() -> None:
    cert_pem, key_pem, key = make_certificate()
    info = certificate_info(cert_pem)
    spaced = ":".join(info.sha1_thumbprint[i : i + 2] for i in range(0, 40, 2)).lower()
    credential = GraphCertificate(private_key=SecretStr(key_pem), thumbprint=spaced)
    assert credential.thumbprint == info.sha1_thumbprint
    header, _ = _verify(credential.signer().assertion(client_id="a", audience=TOKEN_URI), key)
    assert base64.urlsafe_b64decode(header["x5t"] + "=").hex().upper() == info.sha1_thumbprint
    assert "x5t#S256" not in header


def test_an_encrypted_key_needs_its_passphrase() -> None:
    cert_pem, key_pem, key = make_certificate(passphrase=b"correct horse")
    good = GraphCertificate(
        private_key=SecretStr(key_pem),
        private_key_passphrase=SecretStr("correct horse"),
        certificate=cert_pem,
    )
    _verify(good.signer().assertion(client_id="a", audience=TOKEN_URI), key)
    wrong = good.model_copy(update={"private_key_passphrase": SecretStr("hunter2-not-it")})
    with pytest.raises(ConfigurationError, match="could not be read") as raised:
        wrong.signer()
    assert "BEGIN" not in str(raised.value) and "hunter2" not in str(raised.value)


def test_a_certificate_of_another_key_or_a_wrong_thumbprint_is_refused() -> None:
    cert_pem, _, _ = make_certificate()
    _, other_key, _ = make_certificate()
    with pytest.raises(ConfigurationError, match="does not belong"):
        GraphCertificate(private_key=SecretStr(other_key), certificate=cert_pem).signer()
    cert_pem, key_pem, _ = make_certificate()
    with pytest.raises(ConfigurationError, match="does not match"):
        GraphCertificate(
            private_key=SecretStr(key_pem), certificate=cert_pem, thumbprint="AB" * 20
        ).signer()


def test_single_line_pem_values_from_a_secret_store_are_accepted() -> None:
    cert_pem, key_pem, key = make_certificate()
    one_line_key = key_pem.strip().replace("\n", "\\n")
    one_line_cert = '"' + cert_pem.strip().replace("\n", "\\n") + '"'
    assert unescape_pem(one_line_key) == key_pem.strip()
    credential = GraphCertificate(private_key=SecretStr(one_line_key), certificate=one_line_cert)
    _verify(credential.signer().assertion(client_id="a", audience=TOKEN_URI), key)


def test_certificate_info_reports_thumbprints_and_expiry_never_the_key() -> None:
    cert_pem, _, _ = make_certificate(days=20)
    info = certificate_info(cert_pem)
    assert len(info.sha1_thumbprint) == 40 and len(info.sha256_thumbprint) == 64
    assert info.subject == "CN=Careers-Mailbox-Reader"
    assert info.expires_within(30 * 86_400) and not info.expires_within(10 * 86_400)
    with pytest.raises(ValueError, match="not a PEM"):
        certificate_info("-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----")


def test_credential_validation_never_shows_the_key() -> None:
    _, key_pem, _ = make_certificate()
    with pytest.raises(ValidationError) as raised:
        GraphCertificate(private_key=SecretStr(key_pem))  # neither certificate nor thumbprint
    assert "PRIVATE KEY" not in str(raised.value)
    with pytest.raises(ValidationError) as raised:
        GraphCertificate(private_key=SecretStr("not a key at all, secret-material"))
    assert "secret-material" not in str(raised.value)
    with pytest.raises(ValueError, match="40 hex"):
        normalize_thumbprint("abc")
    credential = GraphCertificate(private_key=SecretStr(key_pem), thumbprint="AB" * 20)
    assert "PRIVATE KEY" not in repr(credential)


def test_settings_take_exactly_one_of_secret_and_certificate() -> None:
    cert_pem, key_pem, _ = make_certificate()
    certificate = GraphCertificate(private_key=SecretStr(key_pem), certificate=cert_pem)
    base = {"tenant_id": "contoso.onmicrosoft.com", "client_id": "c", "mailbox": "a@contoso.com"}
    with pytest.raises(ValidationError, match="exactly one"):
        OutlookSettings(**base)
    with pytest.raises(ValidationError, match="exactly one"):
        OutlookSettings(**base, client_secret=SecretStr("s"), certificate=certificate)
    assert OutlookSettings(**base, certificate=certificate).credential_kind == "certificate"
    assert OutlookSettings(**base, client_secret=SecretStr("s")).credential_kind == (
        "client_secret"
    )


class Graph:
    def __init__(self, *, fail: int | None = None, retry_after: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail
        self.retry_after = retry_after
        self.token_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status,
                    json={"error": "invalid_client", "echo": request.content.decode()},
                )
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if self.fail:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(self.fail, headers=headers, json={"error": {"code": "x"}})
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/a%40contoso.com/"
                "mailFolders/inbox/messages?$top=50&$skip=50&$filter=x&$orderby=receivedDateTime+asc",
            },
        )

    def client(self, settings: OutlookSettings) -> OutlookMailboxClient:
        return OutlookMailboxClient(
            settings, client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )

    def grants(self) -> list[dict[str, list[str]]]:
        return [
            parse_qs(r.content.decode())
            for r in self.requests
            if r.url.host == "login.microsoftonline.com"
        ]

    def listings(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "graph.microsoft.com"]


def certificate_settings(**extra: Any) -> tuple[OutlookSettings, rsa.RSAPrivateKey, str]:
    cert_pem, key_pem, key = make_certificate()
    settings = OutlookSettings(
        tenant_id="contoso.onmicrosoft.com",
        client_id="app-id",
        certificate=GraphCertificate(private_key=SecretStr(key_pem), certificate=cert_pem),
        mailbox="careers@contoso.com",
        **extra,
    )
    return settings, key, key_pem


async def test_app_only_access_sends_a_signed_assertion_and_no_secret() -> None:
    settings, key, _ = certificate_settings()
    graph = Graph()
    await graph.client(settings).list_messages()
    [grant] = graph.grants()
    assert grant["grant_type"] == ["client_credentials"]
    assert grant["client_assertion_type"] == [CLIENT_ASSERTION_TYPE]
    assert "client_secret" not in grant
    _, claims = _verify(grant["client_assertion"][0], key)
    assert claims["aud"] == settings.token_uri
    assert grant["scope"] == ["https://graph.microsoft.com/.default"]


async def test_delegated_refresh_also_authenticates_with_the_certificate() -> None:
    settings, key, _ = certificate_settings(refresh_token=SecretStr("refresh"))
    graph = Graph()
    await graph.client(settings).list_messages()
    [grant] = graph.grants()
    assert grant["grant_type"] == ["refresh_token"]
    assert "client_secret" not in grant
    _verify(grant["client_assertion"][0], key)


async def test_a_refused_token_request_names_neither_key_nor_assertion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings, _, key_pem = certificate_settings()
    graph = Graph()
    graph.token_status = 401
    caplog.set_level(logging.DEBUG)
    with pytest.raises(EmailDeliveryError) as raised:
        await graph.client(settings).list_messages()
    message = str(raised.value)
    assert message == "Outlook OAuth token request failed (HTTPStatusError)"
    assertion = graph.grants()[0]["client_assertion"][0]
    for text in (message, caplog.text):
        assert assertion not in text
        assert key_pem.splitlines()[1] not in text


async def test_the_missing_cryptography_package_is_a_configuration_error_naming_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = certificate_settings()
    for name in [module for module in sys.modules if module.startswith("cryptography")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(ConfigurationError, match=r"algen-agent-tools\[outlook\]"):
        OutlookMailboxClient(settings)


def test_received_filter_strings() -> None:
    after = datetime(2026, 1, 1, tzinfo=UTC)
    before = datetime(2026, 2, 1, 5, 30, tzinfo=UTC) + timedelta(hours=0)
    ist = datetime(2026, 2, 1, 11, 0).astimezone().replace(tzinfo=None)
    assert received_filter(after) == "receivedDateTime ge 2026-01-01T00:00:00Z"
    assert received_filter(None, before) == "receivedDateTime lt 2026-02-01T05:30:00Z"
    assert received_filter(after, before, unread_only=True) == (
        "receivedDateTime ge 2026-01-01T00:00:00Z and receivedDateTime lt "
        "2026-02-01T05:30:00Z and isRead eq false"
    )
    from datetime import timezone

    india = timezone(timedelta(hours=5, minutes=30))
    assert received_filter(datetime(2026, 1, 1, 5, 30, tzinfo=india)) == (
        "receivedDateTime ge 2026-01-01T00:00:00Z"
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        received_filter(ist)
    with pytest.raises(ValueError, match="before received_before"):
        received_filter(before, after)


async def test_list_messages_filters_and_orders_by_the_received_window() -> None:
    graph = Graph()
    client = graph.client(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="c",
            client_secret=SecretStr("s"),
            mailbox="a@contoso.com",
        )
    )
    page = await client.list_messages(
        received_after=datetime(2026, 1, 1, tzinfo=UTC),
        received_before=datetime(2026, 1, 8, tzinfo=UTC),
        order="oldest_first",
        unread_only=True,
        max_results=50,
    )
    params = graph.listings()[0].url.params
    assert params["$filter"] == (
        "receivedDateTime ge 2026-01-01T00:00:00Z and receivedDateTime lt "
        "2026-01-08T00:00:00Z and isRead eq false"
    )
    assert params["$orderby"] == "receivedDateTime asc"
    assert "$search" not in params
    # The page token keeps the filter and order; the mailbox is rebuilt by the client.
    assert page.next_page_token is not None and "http" not in page.next_page_token
    await client.list_messages(page_token=page.next_page_token)
    continued = graph.listings()[-1].url
    assert continued.params["$skip"] == "50"
    assert continued.params["$orderby"] == "receivedDateTime asc"
    assert continued.raw_path.decode().startswith("/v1.0/users/a%40contoso.com/mailFolders/inbox")


@pytest.mark.parametrize(
    "options",
    [
        {"received_after": datetime(2026, 1, 1, tzinfo=UTC)},
        {"received_before": datetime(2026, 1, 1, tzinfo=UTC)},
        {"order": "oldest_first"},
    ],
)
async def test_search_cannot_be_combined_with_dates_or_order(options: dict[str, Any]) -> None:
    graph = Graph()
    client = graph.client(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="c",
            client_secret=SecretStr("s"),
            mailbox="a@contoso.com",
        )
    )
    with pytest.raises(ValueError, match="search cannot be combined"):
        await client.list_messages(search="invoice", **options)
    assert graph.requests == []


async def test_the_tool_takes_aware_datetimes_only() -> None:
    graph = Graph()

    async def open_client(secrets: Any) -> MailClient:
        from algen_agent_tools.mail import EmailAddress

        return MailClient(
            EmailAddress(address="a@contoso.com"),
            graph.client(
                OutlookSettings(
                    tenant_id="contoso.onmicrosoft.com",
                    client_id="c",
                    client_secret=SecretStr("s"),
                    mailbox="a@contoso.com",
                )
            ),
        )

    toolkit = outlook_toolkit(client=open_client)
    tool = next(t for t in toolkit.tools if t.definition.name == "outlook.list_messages")
    await tool.execute(
        {"received_after": "2026-01-01T00:00:00+05:30", "order": "oldest_first"},
        fake_tool_context(tool),
    )
    assert graph.listings()[0].url.params["$filter"] == ("receivedDateTime ge 2025-12-31T18:30:00Z")
    with pytest.raises(ValidationError):
        await tool.execute({"received_after": "2026-01-01T00:00:00"}, fake_tool_context(tool))


@pytest.mark.parametrize(("status", "header", "wait"), [(429, "7", 7.0), (503, None, 10.0)])
async def test_throttling_carries_graphs_wait(status: int, header: str | None, wait: float) -> None:
    graph = Graph(fail=status, retry_after=header)
    client = graph.client(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="c",
            client_secret=SecretStr("s"),
            mailbox="a@contoso.com",
        )
    )
    with pytest.raises(EmailDeliveryError) as raised:
        await client.list_messages()
    assert isinstance(raised.value, GraphThrottled)
    assert isinstance(raised.value, ToolNotAttemptedError)
    assert raised.value.retry_after_seconds == wait
    assert raised.value.status_code == status
    assert str(raised.value) == "Outlook inbox read failed (HTTPStatusError)"

    graph.fail = 500
    with pytest.raises(EmailDeliveryError) as other:
        await client.list_messages()
    assert not isinstance(other.value, GraphThrottled)


def test_retry_after_reads_seconds_and_dates_within_bounds() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert retry_after_seconds(httpx.Headers({"Retry-After": "3"})) == 3.0
    assert (
        retry_after_seconds(
            httpx.Headers({"Retry-After": "Thu, 01 Jan 2026 00:00:30 GMT"}), now=now
        )
        == 30.0
    )
    assert retry_after_seconds(httpx.Headers({"Retry-After": "99999"})) == 3600.0
    assert retry_after_seconds(httpx.Headers({"Retry-After": "soon"})) == 10.0
    assert retry_after_seconds(httpx.Headers({})) == 10.0


async def test_the_toolkit_opens_with_a_certificate_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert_pem, key_pem, _ = make_certificate()
    for name, value in {
        "TENANT_ID": "contoso.onmicrosoft.com",
        "CLIENT_ID": "client",
        "MAILBOX": "careers@contoso.com",
        "PRIVATE_KEY": key_pem.replace("\n", "\\n"),
        "CERTIFICATE": cert_pem,
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_OUTLOOK_{name}", value)
    for name in ("CLIENT_SECRET", "REFRESH_TOKEN", "CERTIFICATE_THUMBPRINT"):
        monkeypatch.delenv(f"ALGEN_TOOLS_OUTLOOK_{name}", raising=False)
    toolkit = outlook_toolkit()
    client: MailClient = await toolkit.client()
    assert isinstance(client.transport, OutlookMailboxClient)
    assert client.transport._settings.credential_kind == "certificate"
    await toolkit.aclose()
