"""The read-only OneDrive and SharePoint files connector against a mocked Microsoft Graph."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from algen_agent_runtime.tools.contracts import SideEffect, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.attachments import AttachmentRef, AttachmentUpload
from algen_agent_tools.documents import DocumentReader
from algen_agent_tools.msgraph import GraphThrottled
from algen_agent_tools.onedrive import (
    FolderRef,
    OneDriveError,
    OneDriveFilesClient,
    OneDriveSettings,
    media_type_of,
    onedrive_toolkit,
    share_token,
)
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools

API = "https://graph.microsoft.com/v1.0"
DRIVE = "b!drive-1"
PDF = b"%PDF-1.7 resume of Jane"
DOWNLOAD = (
    "https://contoso-my.sharepoint.com/_layouts/15/download.aspx?UniqueId=1&tempauth=SECRETGRANT"
)


def folder(item_id: str, name: str, parent: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "folder": {"childCount": 1},
        "parentReference": {"driveId": DRIVE, "path": f"/drives/{DRIVE}/root:{parent}"},
    }


def file(item_id: str, name: str, parent: str, size: int = len(PDF)) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "size": size,
        "file": {"mimeType": "application/pdf"},
        "lastModifiedDateTime": "2024-05-01T10:00:00Z",
        "eTag": f'"{{{item_id}}},1"',
        "cTag": f'"c:{{{item_id}}},1"',
        "parentReference": {"driveId": DRIVE, "path": f"/drives/{DRIVE}/root:{parent}"},
    }


ROOT = folder("ROOT", "Careers", "")
TREE: dict[str, list[list[dict[str, Any]]]] = {
    "ROOT": [
        [folder("F1", "Cabin Crew", "/Careers"), file("A1", "a.pdf", "/Careers")],
        [folder("F2", "Engineering", "/Careers")],
    ],
    "F1": [[file("J1", "jane.pdf", "/Careers/Cabin%20Crew")]],
    "F2": [[folder("F3", "AME", "/Careers/Engineering")]],
    "F3": [[file("B1", "bob.docx", "/Careers/Engineering/AME")]],
}
ITEMS = {item["id"]: item for pages in TREE.values() for page in pages for item in page} | {
    "ROOT": ROOT,
    "OUT": file("OUT", "payroll.pdf", "/Finance"),
}


class Graph:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.download_url: str | None = DOWNLOAD
        self.content = PDF
        self.fail: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if host.endswith(".sharepoint.com"):
            return httpx.Response(200, content=self.content)
        if self.fail:
            return httpx.Response(self.fail, headers={"Retry-After": "2"}, json={"error": {}})
        if path.endswith("/children"):
            item_id = path.split("/items/")[1].split("/")[0]
            pages = TREE.get(item_id, [[]])
            index = int(request.url.params.get("$skiptoken") or 0)
            body: dict[str, Any] = {"value": pages[index]}
            if index + 1 < len(pages):
                body["@odata.nextLink"] = (
                    f"{API}/drives/{DRIVE}/items/{item_id}/children?$top=200&$skiptoken={index + 1}"
                )
            return httpx.Response(200, json=body)
        if path.endswith("/content"):
            return httpx.Response(302, headers={"Location": DOWNLOAD})
        if "/items/" in path:
            item = dict(ITEMS[path.rsplit("/", 1)[1]])
            if "file" in item and self.download_url:
                item["@microsoft.graph.downloadUrl"] = self.download_url
            return httpx.Response(200, json=item)
        # The folder by path, by sharing link or by drive root.
        return httpx.Response(200, json=ROOT)

    def client(self, location: FolderRef | None = None, **settings: Any) -> OneDriveFilesClient:
        return OneDriveFilesClient(
            OneDriveSettings(
                tenant_id="contoso.onmicrosoft.com",
                client_id="app",
                client_secret=SecretStr("client-secret"),
                **settings,
            ),
            location or FolderRef(user="hr@contoso.com", path="/Careers"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )

    def api(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "graph.microsoft.com"]


async def test_the_folder_resolves_by_upn_and_path_and_lists_recursively_with_paging() -> None:
    graph = Graph()
    listing = await graph.client().list_files()
    first = graph.api()[0]
    assert first.url.raw_path.decode().startswith(
        "/v1.0/users/hr%40contoso.com/drive/root:/Careers:"
    )
    assert {entry.path: entry.id for entry in listing.files} == {
        "a.pdf": "A1",
        "Cabin Crew/jane.pdf": "J1",
        "Engineering/AME/bob.docx": "B1",
    }
    assert listing.folders == 4 and not listing.truncated and not listing.depth_limited
    jane = next(entry for entry in listing.files if entry.id == "J1")
    assert (jane.drive_id, jane.size, jane.mime_type) == (DRIVE, len(PDF), "application/pdf")
    assert jane.etag == '"{J1},1"' and jane.ctag == '"c:{J1},1"'
    assert jane.last_modified is not None and jane.last_modified.year == 2024
    # The second page of the root folder was followed.
    assert any(r.url.params.get("$skiptoken") == "1" for r in graph.api())


async def test_depth_and_item_limits_stop_the_listing() -> None:
    graph = Graph()
    shallow = await graph.client().list_files(max_depth=1)
    assert sorted(entry.path for entry in shallow.files) == ["Cabin Crew/jane.pdf", "a.pdf"]
    assert shallow.depth_limited
    capped = await Graph().client().list_files(max_items=1)
    assert len(capped.files) == 1 and capped.truncated
    flat = await Graph().client().list_files(recursive=False)
    assert [entry.path for entry in flat.files] == ["a.pdf"]
    with pytest.raises(ValueError, match="max_depth"):
        await Graph().client().list_files(max_depth=99)


async def test_every_request_is_a_read() -> None:
    graph = Graph()
    client = graph.client()
    await client.list_files()
    await client.download("J1")
    methods = {(r.method, r.url.host) for r in graph.requests}
    assert methods == {
        ("POST", "login.microsoftonline.com"),
        ("GET", "graph.microsoft.com"),
        ("GET", "contoso-my.sharepoint.com"),
    }


async def test_download_uses_the_preauthenticated_url_without_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph = Graph()
    caplog.set_level(logging.INFO, logger="httpx")
    upload = await graph.client().download("J1")
    assert upload == AttachmentUpload("jane.pdf", "application/pdf", PDF)
    [fetch] = [r for r in graph.requests if r.url.host.endswith(".sharepoint.com")]
    assert "authorization" not in fetch.headers
    assert "SECRETGRANT" not in caplog.text


async def test_download_without_a_download_url_follows_the_content_redirect() -> None:
    graph = Graph()
    graph.download_url = None
    upload = await graph.client().download("J1")
    assert upload.data == PDF
    assert any(r.url.path.endswith("/items/J1/content") for r in graph.api())


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/steal?x=1",
        "http://contoso.sharepoint.com/file",
        "https://contoso.sharepoint.com:8443/file",
        "https://sharepoint.com.evil.example/file",
        "https://user@contoso.sharepoint.com/file",
    ],
)
async def test_download_hosts_are_locked_to_microsoft(url: str) -> None:
    graph = Graph()
    graph.download_url = url
    with pytest.raises(OneDriveError, match=r"file download failed \(EgressRefused\)"):
        await graph.client().download("J1")
    assert not [
        r
        for r in graph.requests
        if r.url.host
        not in {
            "login.microsoftonline.com",
            "graph.microsoft.com",
        }
    ]


async def test_download_size_limits() -> None:
    graph = Graph()
    with pytest.raises(OneDriveError, match="size limit"):
        await graph.client(max_download_bytes=5).download("J1")  # Graph's declared size
    graph = Graph()
    ITEMS["J1"]["size"] = 1  # a declared size that understates the content
    try:
        with pytest.raises(OneDriveError, match="size limit"):
            await graph.client().download("J1", max_bytes=10)
    finally:
        ITEMS["J1"]["size"] = len(PDF)


async def test_items_outside_the_configured_folder_are_refused() -> None:
    graph = Graph()
    with pytest.raises(OneDriveError, match="not in the configured folder"):
        await graph.client().download("OUT")
    with pytest.raises(OneDriveError, match="not in the configured folder"):
        await graph.client().get_file("OUT")
    entry = await graph.client().get_file("B1")
    assert entry.path == "Engineering/AME/bob.docx"


async def test_folders_by_drive_item_site_and_sharing_link() -> None:
    graph = Graph()
    await graph.client(FolderRef(drive_id=DRIVE, item_id="ROOT")).list_folder()
    await graph.client(FolderRef(site="contoso.sharepoint.com:/sites/HR", path="/CVs")).root()
    link = "https://contoso.sharepoint.com/:f:/s/HR/EabcDEF?e=1"
    await graph.client(FolderRef(sharing_url=link)).root()
    paths = [r.url.raw_path.decode().split("?")[0] for r in graph.api()]
    assert paths[0] == f"/v1.0/drives/{DRIVE}/items/ROOT"
    assert "/v1.0/sites/contoso.sharepoint.com:/sites/HR:/drive/root:/CVs:" in paths
    assert f"/v1.0/shares/{share_token(link)}/driveItem" in paths
    assert share_token("https://a/b?c") == "u!aHR0cHM6Ly9hL2I_Yw"
    with pytest.raises(ValueError, match="configured Microsoft host"):
        graph.client(FolderRef(sharing_url="https://evil.example/x"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"drives/{DRIVE}/items/ROOT", {"drive_id": DRIVE, "item_id": "ROOT"}),
        (f"/drives/{DRIVE}/root:/Careers/CVs", {"drive_id": DRIVE, "path": "/Careers/CVs"}),
        (
            "users/hr@contoso.com/drive/root:/Careers:",
            {"user": "hr@contoso.com", "path": "/Careers"},
        ),
        ("users/hr@contoso.com/drive/root", {"user": "hr@contoso.com", "path": ""}),
        (
            "sites/contoso.sharepoint.com:/sites/HR:/drive/root:/CVs",
            {"site": "contoso.sharepoint.com:/sites/HR", "path": "/CVs"},
        ),
        (
            "sites/contoso.sharepoint.com,11111111-1111-1111-1111-111111111111,"
            "22222222-2222-2222-2222-222222222222/drive/root:/CVs",
            {
                "site": "contoso.sharepoint.com,11111111-1111-1111-1111-111111111111,"
                "22222222-2222-2222-2222-222222222222",
                "path": "/CVs",
            },
        ),
        (
            "https://contoso.sharepoint.com/:f:/s/HR/x",
            {"sharing_url": "https://contoso.sharepoint.com/:f:/s/HR/x"},
        ),
    ],
)
def test_folder_refs_parse_graph_paths_and_links(text: str, expected: dict[str, str]) -> None:
    assert FolderRef.parse(text) == FolderRef(**expected)


@pytest.mark.parametrize(
    "text",
    [
        "users/hr@contoso.com/drive/root:/../Finance",
        "users/../drive/root:/x",
        "me/drive/root",
        "drives/x/items/..",
        "http://contoso.sharepoint.com/x",
        "users/hr@contoso.com/drive/root:/a:b",
    ],
)
def test_folder_refs_refuse_paths_that_could_escape(text: str) -> None:
    with pytest.raises(ValueError):
        FolderRef.parse(text)


def test_settings_need_exactly_one_credential_and_valid_hosts() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        OneDriveSettings(tenant_id="t.onmicrosoft.com", client_id="c")
    with pytest.raises(ValidationError, match="download_host_suffixes"):
        OneDriveSettings(
            tenant_id="t.onmicrosoft.com",
            client_id="c",
            client_secret=SecretStr("s"),
            download_host_suffixes=("com",),
        )


async def test_throttling_and_failures_name_only_the_cause() -> None:
    graph = Graph()
    graph.fail = 429
    with pytest.raises(OneDriveError) as raised:
        await graph.client().list_files()
    assert isinstance(raised.value, GraphThrottled)
    assert raised.value.retry_after_seconds == 2.0
    assert str(raised.value) == "OneDrive folder lookup failed (HTTPStatusError)"
    graph.fail = 403
    with pytest.raises(OneDriveError) as denied:
        await graph.client().list_files()
    assert not isinstance(denied.value, GraphThrottled)
    assert "client-secret" not in str(denied.value)


def test_media_types_follow_the_content_signature() -> None:
    assert media_type_of(PDF, filename="cv.docx", declared="application/msword") == (
        "application/pdf"
    )
    assert media_type_of(b"\xd0\xcf\x11\xe0rest", filename="cv.doc", declared=None) == (
        "application/msword"
    )
    assert media_type_of(b"plain", filename="cv.txt", declared="application/octet-stream") == (
        "text/plain"
    )


def test_the_plugin_describes_read_only_tools() -> None:
    tools = plugin_tools("algen_agent_tools.onedrive")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "onedrive.list_folder": SideEffect.READ,
    }
    assert "ALGEN_TOOLS_ONEDRIVE_FOLDER" in tools["onedrive.list_folder"].env

    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        raise AssertionError

    toolkit = onedrive_toolkit(artifact_sink=sink, documents=DocumentReader())
    assert_toolkit_contract(toolkit)
    assert {tool.definition.name: tool.definition.side_effect for tool in toolkit.tools} == {
        "onedrive.list_folder": SideEffect.READ,
        "onedrive.get_file": SideEffect.READ,
        "onedrive.read_file": SideEffect.READ,
    }


async def test_tools_list_a_folder_page_by_page_and_save_a_file() -> None:
    graph = Graph()
    stored: list[AttachmentUpload] = []

    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        stored.append(upload)
        return AttachmentRef(
            artifact_id="art-1", filename=upload.filename, media_type=upload.media_type
        )

    async def open_client(secrets: Any) -> OneDriveFilesClient:
        return graph.client()

    toolkit = onedrive_toolkit(client=open_client, artifact_sink=sink)
    tools = {tool.definition.name: tool for tool in toolkit.tools}
    listing = tools["onedrive.list_folder"]
    page = await listing.execute({}, fake_tool_context(listing))
    assert [entry["name"] for entry in page["entries"]] == ["Cabin Crew", "a.pdf"]
    assert page["next_page_token"] == "ROOT|1"
    second = await listing.execute(
        {"page_token": page["next_page_token"]}, fake_tool_context(listing)
    )
    assert [entry["name"] for entry in second["entries"]] == ["Engineering"]
    inside = await listing.execute({"folder_id": "F1"}, fake_tool_context(listing))
    assert inside["entries"][0]["path"] == "Cabin Crew/jane.pdf"
    with pytest.raises(OneDriveError, match=r"folder listing failed \(ValueError\)"):
        await listing.execute({"page_token": "F1|1"}, fake_tool_context(listing))

    get_file = tools["onedrive.get_file"]
    result = await get_file.execute({"file_id": "J1"}, fake_tool_context(get_file))
    assert result["artifact_id"] == "art-1" and stored[0].data == PDF
    with pytest.raises(ValidationError):
        await get_file.execute({"file_id": ".."}, fake_tool_context(get_file))
