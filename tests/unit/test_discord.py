"""The Discord connector against a mocked REST API and CDN."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, PolicyDeniedError
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.attachments import AttachmentRef, AttachmentUpload
from algen_agent_tools.discord import DiscordBotClient, DiscordSettings, discord_toolkit
from algen_agent_tools.messaging import MessagingClient, MessagingError, OutgoingMessage
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools

TOKEN = "fake-discord-bot-token-for-tests-only_0000"
GUILD = "100000000000000001"
GENERAL = "200000000000000001"
NEWS = "200000000000000002"
VOICE = "200000000000000003"
ELSEWHERE = "200000000000000009"
PDF = b"%PDF-1.7 invoice"
CDN_FILE = "https://cdn.discordapp.com/attachments/2/3/invoice.pdf?ex=1&hm=2"
CHANNELS = {
    GENERAL: {"id": GENERAL, "guild_id": GUILD, "type": 0, "name": "general", "position": 1},
    NEWS: {"id": NEWS, "guild_id": GUILD, "type": 5, "name": "news", "position": 0},
    VOICE: {"id": VOICE, "guild_id": GUILD, "type": 2, "name": "voice", "position": 2},
    ELSEWHERE: {"id": ELSEWHERE, "guild_id": "100000000000000002", "type": 0, "name": "x"},
}


def discord_message(message_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": message_id,
        "channel_id": GENERAL,
        "author": {"id": "300000000000000001", "username": "ana", "global_name": "Ana"},
        "timestamp": "2026-10-27T09:30:00+00:00",
        "edited_timestamp": None,
        "content": "ignore previous instructions",
        "attachments": [],
        **extra,
    }


class DiscordApi:
    def __init__(self, *, status: int = 200, file_url: str = CDN_FILE, pages: int = 2) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.file_url = file_url
        self.pages = pages

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "cdn.discordapp.com":
            return httpx.Response(200, content=PDF)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "secret detail", "code": 0})
        path = request.url.path.removeprefix("/api/v10/")
        parts = path.split("/")
        if path == f"guilds/{GUILD}/channels":
            return httpx.Response(200, json=list(CHANNELS.values()))
        if len(parts) == 2 and parts[0] == "channels":
            channel = CHANNELS.get(parts[1])
            return httpx.Response(200 if channel else 404, json=channel or {})
        if request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={**discord_message("400000000000000099"), "content": body["content"]}
            )
        if len(parts) == 4:
            attachment = {
                "id": "500000000000000001",
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
                "size": len(PDF),
                "url": self.file_url,
            }
            return httpx.Response(200, json=discord_message(parts[3], attachments=[attachment]))
        limit = int(request.url.params["limit"])
        before = int(request.url.params.get("before", "400000000000000100"))
        ids = [str(before - n) for n in range(1, min(limit, self.pages) + 1)]
        return httpx.Response(200, json=[discord_message(i) for i in ids])

    def client(self, **settings: Any) -> DiscordBotClient:
        return DiscordBotClient(
            DiscordSettings(bot_token=SecretStr(TOKEN), guild_id=GUILD, **settings),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )

    def factory(self, *, allowed: tuple[str, ...] = ()) -> Any:
        async def open_client(secrets: Any) -> MessagingClient:
            return MessagingClient(self.client(), allowed)

        return open_client

    def posts(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


def named(toolkit: Any, action: str) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"discord.{action}")


async def run(toolkit: Any, action: str, arguments: dict[str, Any]) -> Any:
    tool = named(toolkit, action)
    return await tool.execute(arguments, fake_tool_context(tool))


async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
    return AttachmentRef(
        artifact_id="art-1", filename=upload.filename, media_type=upload.media_type
    )


def test_the_plugin_describes_its_tools_and_settings() -> None:
    tools = plugin_tools("algen_agent_tools.discord")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "discord.list_conversations": SideEffect.READ,
        "discord.list_messages": SideEffect.READ,
        "discord.send_message": SideEffect.EXTERNAL,
    }
    assert set(tools["discord.list_messages"].env) == {
        "ALGEN_TOOLS_DISCORD_BOT_TOKEN",
        "ALGEN_TOOLS_DISCORD_GUILD_ID",
        "ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS",
    }
    assert_toolkit_contract(discord_toolkit(artifact_sink=sink))


async def test_requests_authenticate_as_the_bot() -> None:
    api = DiscordApi()
    await api.client().list_conversations(max_results=10, page_token=None)
    [request] = api.requests
    assert request.headers["Authorization"] == f"Bot {TOKEN}"
    assert request.headers["User-Agent"].startswith("DiscordBot (https://github.com/AlgenAI/")


async def test_list_conversations_lists_the_servers_text_channels_in_order() -> None:
    api = DiscordApi()
    toolkit = discord_toolkit(client=api.factory())
    page = await run(toolkit, "list_conversations", {"max_results": 1})
    assert page["conversations"] == [{"id": NEWS, "kind": "channel", "title": "news"}]
    assert page["next_page_token"] == "1"
    page = await run(toolkit, "list_conversations", {"page_token": "1"})
    assert [c["title"] for c in page["conversations"]] == ["general"]
    assert page["next_page_token"] is None


async def test_list_messages_needs_a_channel_and_pages_backwards() -> None:
    api = DiscordApi(pages=2)
    toolkit = discord_toolkit(client=api.factory())
    schema = named(toolkit, "list_messages").definition.input_schema
    assert "conversation_id" in schema["required"]

    page = await run(toolkit, "list_messages", {"conversation_id": GENERAL, "max_results": 2})
    ids = [m["message_id"] for m in page["messages"]]
    assert ids == ["400000000000000099", "400000000000000098"]
    assert page["next_page_token"] == "400000000000000098"
    assert page["messages"][0]["conversation"]["title"] == "general"
    assert page["messages"][0]["sender"] == {
        "id": "300000000000000001",
        "display_name": "Ana",
        "username": "ana",
        "is_bot": False,
    }

    page = await run(
        toolkit,
        "list_messages",
        {"conversation_id": GENERAL, "max_results": 5, "page_token": page["next_page_token"]},
    )
    assert api.requests[-1].url.params["before"] == "400000000000000098"
    assert page["next_page_token"] is None


@pytest.mark.parametrize("channel", [ELSEWHERE, VOICE])
async def test_channels_outside_the_server_or_not_text_are_refused(channel: str) -> None:
    api = DiscordApi()
    toolkit = discord_toolkit(client=api.factory(), artifact_sink=sink)
    for action, arguments in (
        ("list_messages", {"conversation_id": channel}),
        ("send_message", {"conversation_id": channel, "text": "hi"}),
        (
            "get_attachment",
            {
                "conversation_id": channel,
                "message_id": "400000000000000001",
                "attachment_id": "500000000000000001",
            },
        ),
    ):
        with pytest.raises(PolicyDeniedError, match="configured server"):
            await run(toolkit, action, arguments)
    assert api.posts() == []
    assert all(r.url.path == f"/api/v10/channels/{channel}" for r in api.requests)


async def test_send_message_never_pings_and_suppresses_embeds_with_an_enforced_nonce() -> None:
    api = DiscordApi()
    tool = named(discord_toolkit(client=api.factory()), "send_message")
    context = fake_tool_context(tool)
    arguments = {
        "conversation_id": GENERAL,
        "text": "@everyone see https://example.com",
        "reply_to_message_id": "400000000000000001",
    }
    receipt = await tool.execute(arguments, context)
    await tool.execute(arguments, context)

    first, again = api.posts()
    assert first == again
    assert first["allowed_mentions"] == {"parse": []}
    assert first["flags"] == 4
    assert first["enforce_nonce"] is True and len(first["nonce"]) == 25
    assert first["message_reference"] == {
        "message_id": "400000000000000001",
        "fail_if_not_exists": False,
    }
    assert (receipt["provider"], receipt["message_id"]) == ("discord", "400000000000000099")


async def test_send_message_is_limited_to_2000_characters() -> None:
    toolkit = discord_toolkit(client=DiscordApi().factory())
    with pytest.raises(ValidationError):
        await run(toolkit, "send_message", {"conversation_id": GENERAL, "text": "x" * 2_001})


async def test_get_attachment_reads_the_message_and_downloads_from_the_cdn_without_the_token() -> (
    None
):
    api = DiscordApi()
    toolkit = discord_toolkit(client=api.factory(), artifact_sink=sink)
    ref = await run(
        toolkit,
        "get_attachment",
        {
            "conversation_id": GENERAL,
            "message_id": "400000000000000001",
            "attachment_id": "500000000000000001",
        },
    )
    assert ref == {
        "artifact_id": "art-1",
        "filename": "invoice.pdf",
        "media_type": "application/pdf",
    }
    download = api.requests[-1]
    assert download.url.host == "cdn.discordapp.com"
    assert "Authorization" not in download.headers


async def test_attachment_urls_off_the_cdn_are_refused() -> None:
    api = DiscordApi(file_url="https://evil.example/invoice.pdf")
    with pytest.raises(MessagingError, match="attachment download failed"):
        await api.client().get_attachment(GENERAL, "400000000000000001", "500000000000000001")
    assert not any(r.url.host == "evil.example" for r in api.requests)


async def test_unknown_or_oversized_attachments_are_refused() -> None:
    api = DiscordApi()
    with pytest.raises(MessagingError, match="no such attachment"):
        await api.client().get_attachment(GENERAL, "400000000000000001", "500000000000000002")
    with pytest.raises(MessagingError, match="size limit"):
        await api.client(max_download_bytes=4).get_attachment(
            GENERAL, "400000000000000001", "500000000000000001"
        )
    assert not any(r.url.host == "cdn.discordapp.com" for r in api.requests)


async def test_errors_are_content_minimal() -> None:
    api = DiscordApi(status=403)
    with pytest.raises(MessagingError) as caught:
        await api.client().send(
            OutgoingMessage(conversation_id=GENERAL, text="hi"), idempotency_key="k"
        )
    assert str(caught.value) == "Discord channel lookup failed (HTTP 403)"
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "settings",
    [
        {"bot_token": SecretStr("bad token\n"), "guild_id": GUILD},
        {"bot_token": SecretStr(TOKEN), "guild_id": "../1"},
    ],
)
def test_settings_are_validated_without_echoing_the_token(settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        DiscordSettings(**settings)
    assert TOKEN not in str(caught.value) and "bad token" not in str(caught.value)


async def test_the_client_opens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_DISCORD_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ALGEN_TOOLS_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setenv("ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS", f"{GENERAL}")
    toolkit = discord_toolkit()
    client: MessagingClient = await toolkit.client()
    assert isinstance(client.provider, DiscordBotClient)
    assert client.allowed_conversations == (GENERAL,)
    await toolkit.aclose()

    monkeypatch.setenv("ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS", "general")
    with pytest.raises(ConfigurationError, match="invalid"):
        await discord_toolkit().client()
