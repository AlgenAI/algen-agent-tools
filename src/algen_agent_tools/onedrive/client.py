"""Microsoft Graph files, read-only: list and download the files below one OneDrive or
SharePoint folder with the app's own credentials.

Every request is a ``GET``: the client has no code path that creates, changes, moves, shares or
deletes anything. Downloads follow Graph's pre-authenticated download URL only to the Microsoft
download hosts the settings name (``*.sharepoint.com`` by default).
"""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.auth import OAuthClientCredentials
from algen_agent_tools._core.errors import ResponseTooLarge, failure, not_attempted
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.msgraph.credentials import (
    GraphCertificate,
    check_one_credential,
    client_authentication,
)
from algen_agent_tools.msgraph.throttling import throttled, throttled_response

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_ID = re.compile(r"^[A-Za-z0-9!_=+,-][A-Za-z0-9!_=+.,-]{0,499}$")
_UPN = re.compile(r"^[^\s/\\?#:@]+@[A-Za-z0-9.-]+$")
_SITE_ID = re.compile(r"^[A-Za-z0-9.-]+(,[0-9A-Fa-f-]{36}){2}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_ITEM_FIELDS = "id,name,size,file,folder,lastModifiedDateTime,eTag,cTag,parentReference,webUrl"
_PAGE_SIZE = 200
_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".txt": "text/plain",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".bmp": "image/bmp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class OneDriveError(Exception):
    """A OneDrive or SharePoint request failed. The message names the operation and the cause's
    type, never Graph's response or a file's content."""


def _check_path(path: str) -> str:
    """A folder path below a drive's root, as ``/A/B``: no ``.`` or ``..`` segments, no ``:``
    (Graph's path delimiter) and no backslashes."""
    cleaned = path.strip()
    if len(cleaned) > 1_000 or ":" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise ValueError("path must be a folder path such as /Careers/Resumes")
    segments = [segment for segment in cleaned.split("/") if segment]
    if any(segment.strip() in {".", ".."} for segment in segments):
        raise ValueError("path must not contain . or .. segments")
    return "/" + "/".join(segments) if segments else ""


def _encoded_path(path: str) -> str:
    return "/".join(quote(segment, safe="") for segment in path.split("/") if segment)


class FolderRef(BaseModel):
    """Where the files are: exactly one of

    - ``drive_id`` with ``item_id`` (the folder's id) or ``path`` (below the drive's root);
    - ``user`` (a UPN) with ``path``: a folder in that user's OneDrive;
    - ``site`` (a site id ``host,guid,guid`` or ``host:/sites/name``) with ``path``: a folder in
      the site's default document library;
    - ``sharing_url``: a sharing link to the folder, on a configured download host.

    An empty ``path`` is the drive's root. :meth:`parse` reads the same forms from one string.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    drive_id: str | None = Field(default=None, max_length=500)
    item_id: str | None = Field(default=None, max_length=500)
    user: str | None = Field(default=None, max_length=320)
    site: str | None = Field(default=None, max_length=1_000)
    path: str = Field(default="", max_length=1_000)
    sharing_url: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _check(self) -> FolderRef:
        forms = [self.drive_id, self.user, self.site, self.sharing_url]
        if sum(value is not None for value in forms) != 1:
            raise ValueError("name the folder by exactly one of drive_id, user, site, sharing_url")
        object.__setattr__(self, "path", _check_path(self.path))
        if self.item_id is not None and (self.drive_id is None or self.path):
            raise ValueError("item_id needs drive_id and no path")
        for value in (self.drive_id, self.item_id):
            if value is not None and not _ID.fullmatch(value):
                raise ValueError("drive_id and item_id must be Graph ids")
        if self.user is not None and not _UPN.fullmatch(self.user):
            raise ValueError("user must be a user principal name such as hr@contoso.com")
        if self.site is not None:
            host, separator, site_path = self.site.partition(":/")
            if separator:
                if not _HOST.fullmatch(host) or not _check_path(site_path):
                    raise ValueError("site must be host:/sites/name or a site id")
            elif not _SITE_ID.fullmatch(self.site):
                raise ValueError("site must be host:/sites/name or a site id")
        if self.sharing_url is not None:
            parts = urlsplit(self.sharing_url)
            if parts.scheme != "https" or not parts.hostname:
                raise ValueError("sharing_url must be an https sharing link")
            if self.path:
                raise ValueError("a sharing_url names the folder itself; path must be empty")
        return self

    @classmethod
    def parse(cls, text: str) -> FolderRef:
        """A folder from one string: an https sharing link, or a Graph path
        ``drives/{drive-id}/items/{item-id}``, ``drives/{drive-id}/root:/{path}``,
        ``users/{upn}/drive/root:/{path}``, ``sites/{site-id}/drive/root:/{path}`` or
        ``sites/{host}:/{site-path}:/drive/root:/{path}`` (a leading ``/`` is ignored)."""
        value = text.strip()
        if value.startswith("https://"):
            return cls(sharing_url=value)
        value = value.lstrip("/")
        forms: tuple[tuple[str, str], ...] = (
            (r"drives/(?P<drive_id>[^/]+)/items/(?P<item_id>[^/]+)/?", "drive_item"),
            (r"drives/(?P<drive_id>[^/]+)/root(?::(?P<path>/.*?))?:?", "drive_path"),
            (r"users/(?P<user>[^/]+)/drive/root(?::(?P<path>/.*?))?:?", "user"),
            (r"sites/(?P<site>[^/:]+:/.+?):/drive/root(?::(?P<path>/.*?))?:?", "site"),
            (r"sites/(?P<site>[^/:]+)/drive/root(?::(?P<path>/.*?))?:?", "site"),
        )
        for pattern, _ in forms:
            match = re.fullmatch(pattern, value)
            if match:
                found = {key: item for key, item in match.groupdict().items() if item}
                return cls(**found)
        raise ValueError(
            "folder must be a sharing link or a Graph path such as "
            "users/hr@contoso.com/drive/root:/Careers"
        )

    def describe(self) -> str:
        """A short, credential-free description for logs and status pages."""
        if self.sharing_url is not None:
            parts = urlsplit(self.sharing_url)
            return f"sharing link on {parts.hostname}"
        if self.item_id is not None:
            return f"drive {self.drive_id}, item {self.item_id}"
        owner = (
            f"drive {self.drive_id}"
            if self.drive_id
            else (f"{self.user}'s OneDrive" if self.user else f"site {self.site}")
        )
        return f"{owner}, {self.path or '/'}"


class OneDriveSettings(BaseModel):
    """App-only (application) access to OneDrive and SharePoint files through Microsoft Graph.

    The app authenticates with exactly one of ``client_secret`` and ``certificate``. Grant it
    ``Sites.Selected`` (``read`` on the site) or ``Files.SelectedOperations.Selected``
    (``read`` on the folder); see the connector's documentation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=253)
    client_id: str = Field(min_length=1, max_length=1_000)
    client_secret: SecretStr | None = None
    certificate: GraphCertificate | None = None
    authority: str = "https://login.microsoftonline.com"
    api_base_url: str = "https://graph.microsoft.com/v1.0"
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_download_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)
    """The largest file :meth:`OneDriveFilesClient.download` reads."""
    download_host_suffixes: tuple[str, ...] = (".sharepoint.com",)
    """The hosts Graph's pre-authenticated download URLs (and sharing links) may be on. Add
    ``.sharepoint.us`` or ``.sharepoint.cn`` for a national cloud."""

    @model_validator(mode="after")
    def _check(self) -> OneDriveSettings:
        if not _TENANT.fullmatch(self.tenant_id):
            raise ValueError("tenant_id must be a tenant id or domain")
        check_one_credential(self.client_secret, self.certificate)
        if not self.download_host_suffixes or any(
            not suffix.startswith(".") or suffix.count(".") < 2
            for suffix in self.download_host_suffixes
        ):
            raise ValueError("download_host_suffixes must be domains such as .sharepoint.com")
        return self

    @property
    def token_uri(self) -> str:
        return f"{self.authority.rstrip('/')}/{self.tenant_id}/oauth2/v2.0/token"


class DriveEntry(BaseModel):
    """One file or folder below the configured folder."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    drive_id: str
    name: str
    path: str
    """Its path relative to the configured folder, ``/`` separated, starting with its parent
    folders (``Cabin Crew/2024/jane-doe.pdf``)."""
    is_folder: bool = False
    size: int = 0
    mime_type: str | None = None
    last_modified: datetime | None = None
    etag: str | None = None
    ctag: str | None = None
    """Changes when the file's content changes (the eTag also changes on a rename)."""
    web_url: str | None = None


class DriveListing(BaseModel):
    """The files a recursive listing found, and whether it stopped at a limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    files: tuple[DriveEntry, ...] = ()
    folders: int = 0
    truncated: bool = False
    """The listing reached ``max_items``; more files exist."""
    depth_limited: bool = False
    """Some folders were deeper than ``max_depth`` and not listed."""


class DriveFolderPage(BaseModel):
    """One page of a folder's direct children."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: tuple[DriveEntry, ...] = ()
    next_page_token: str | None = None


@dataclass(frozen=True)
class _Root:
    drive_id: str
    item_id: str
    tail: str
    """The folder's path below its drive's root, as Graph's ``parentReference.path`` ends."""


def _tail(parent_path: str | None) -> str | None:
    """The part of a ``parentReference.path`` after ``root:``, decoded: ``/Careers/2024``."""
    if parent_path is None:
        return None
    _, separator, tail = parent_path.partition("root:")
    return unquote(tail).rstrip("/") if separator else None


def _segment(value: str) -> str:
    """One path segment; ``!`` stays as Graph writes drive ids (``b!...``)."""
    return quote(value, safe="!")


def share_token(sharing_url: str) -> str:
    """A sharing URL as Graph's ``shares/{token}`` id: ``u!`` and its unpadded base64url."""
    encoded = base64.urlsafe_b64encode(sharing_url.encode()).rstrip(b"=").decode("ascii")
    return f"u!{encoded}"


def media_type_of(data: bytes, *, filename: str, declared: str | None) -> str:
    """The file's media type: the type its leading bytes declare when they have a known
    signature (as the documents and attachments checks read them), otherwise the type its name
    implies, otherwise Graph's declared type."""
    from algen_agent_tools.documents.inspection.core import sniff_suffix

    sniffed = sniff_suffix(data)
    if sniffed is not None and sniffed in _MEDIA_TYPES:
        return _MEDIA_TYPES[sniffed]
    clean = (declared or "").partition(";")[0].strip().casefold()
    if "/" in clean and clean != "application/octet-stream":
        return clean
    suffix = ("." + filename.rsplit(".", 1)[-1].casefold()) if "." in filename else ""
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


def _name(value: Any) -> str:
    return re.split(r"[/\\]", str(value or ""))[-1].strip()


class OneDriveFilesClient:
    """A read-only Graph client for the files below one folder."""

    def __init__(
        self,
        settings: OneDriveSettings,
        folder: FolderRef,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._folder = folder
        if folder.sharing_url is not None:
            host = (urlsplit(folder.sharing_url).hostname or "").casefold()
            if not any(host.endswith(suffix) for suffix in settings.download_host_suffixes):
                raise ValueError("sharing_url is not on a configured Microsoft host")
        self._http = HostLockedClient(
            settings.api_base_url,
            also=(settings.token_uri,),
            timeout_seconds=settings.timeout_seconds,
            max_response_bytes=8_000_000,  # listings and item metadata, never file content
            client=client,
        )
        self._oauth = OAuthClientCredentials(
            self._http,
            token_uri=settings.token_uri,
            client_id=settings.client_id,
            scope=GRAPH_SCOPE,
            client_auth=client_authentication(
                client_id=settings.client_id,
                token_uri=settings.token_uri,
                client_secret=settings.client_secret,
                certificate=settings.certificate,
                extra="onedrive",
            ),
            on_error=lambda exc: OneDriveError(failure("OneDrive", "OAuth token request", exc)),
        )
        self._root: _Root | None = None

    @property
    def folder(self) -> FolderRef:
        return self._folder

    async def _get(self, path_or_url: str, **params: str) -> Mapping[str, Any]:
        url = path_or_url if path_or_url.startswith("https://") else self._http.url(path_or_url)
        token = await self._oauth.token()
        response = await self._http.get(
            url, headers={"Authorization": f"Bearer {token}"}, params=params or None
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Graph returned an unexpected response")
        return payload

    def _error(self, operation: str, cause: BaseException) -> OneDriveError:
        error = OneDriveError(failure("OneDrive", operation, cause))
        response = throttled_response(cause)
        if response is not None:
            return not_attempted(throttled(error, response))
        return error

    def _folder_path(self) -> str:
        folder = self._folder
        if folder.sharing_url is not None:
            return f"shares/{share_token(folder.sharing_url)}/driveItem"
        if folder.item_id is not None:
            return f"drives/{_segment(str(folder.drive_id))}/items/{_segment(folder.item_id)}"
        if folder.drive_id is not None:
            base = f"drives/{_segment(folder.drive_id)}/root"
        elif folder.user is not None:
            base = f"users/{_segment(folder.user)}/drive/root"
        else:
            site = str(folder.site)
            host, separator, site_path = site.partition(":/")
            site_part = f"{host}:/{_encoded_path(site_path)}:" if separator else _segment(site)
            base = f"sites/{site_part}/drive/root"
        return f"{base}:/{_encoded_path(folder.path)}:" if folder.path else base

    async def root(self) -> _Root:
        """Resolve the configured folder once: its drive, id and place in the drive."""
        if self._root is not None:
            return self._root
        try:
            item = await self._get(self._folder_path(), **{"$select": _ITEM_FIELDS})
            if "folder" not in item:
                raise OneDriveError("the configured OneDrive location is not a folder")
            parent = item.get("parentReference") or {}
            drive_id = str(parent.get("driveId") or self._folder.drive_id or "")
            if not drive_id or not _ID.fullmatch(drive_id) or not _ID.fullmatch(str(item["id"])):
                raise OneDriveError("Graph did not return the folder's drive")
            parent_tail = _tail(parent.get("path"))
            tail = "" if parent_tail is None else f"{parent_tail}/{_name(item.get('name'))}"
        except OneDriveError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise self._error("folder lookup", exc) from exc
        self._root = _Root(drive_id=drive_id, item_id=str(item["id"]), tail=tail)
        return self._root

    def _entry(self, item: Mapping[str, Any], drive_id: str, parent: str) -> DriveEntry:
        name = _name(item.get("name"))
        file = item.get("file") or {}
        modified = item.get("lastModifiedDateTime")
        return DriveEntry(
            id=str(item["id"]),
            drive_id=drive_id,
            name=name,
            path=f"{parent}/{name}" if parent else name,
            is_folder="folder" in item,
            size=max(0, int(item.get("size") or 0)),
            mime_type=str(file.get("mimeType")) if file.get("mimeType") else None,
            last_modified=datetime.fromisoformat(str(modified)) if modified else None,
            etag=str(item["eTag"]) if item.get("eTag") else None,
            ctag=str(item["cTag"]) if item.get("cTag") else None,
            web_url=str(item["webUrl"]) if item.get("webUrl") else None,
        )

    async def _children(
        self, drive_id: str, item_id: str, parent: str, page_url: str | None = None
    ) -> tuple[list[DriveEntry], str | None]:
        if page_url is None:
            payload = await self._get(
                f"drives/{_segment(drive_id)}/items/{_segment(item_id)}/children",
                **{"$select": _ITEM_FIELDS, "$top": str(_PAGE_SIZE)},
            )
        else:
            payload = await self._get(page_url)  # Graph's link carries the query
        entries = [
            self._entry(item, drive_id, parent)
            for item in payload.get("value") or ()
            if isinstance(item, Mapping) and item.get("id") and ("file" in item or "folder" in item)
        ]
        next_link = payload.get("@odata.nextLink")
        if next_link and not str(next_link).startswith(self._http.url("drives/")):
            raise ValueError("Graph returned a page link outside the drive")
        return entries, str(next_link) if next_link else None

    async def iter_files(
        self,
        *,
        recursive: bool = True,
        max_depth: int = 10,
        max_items: int = 10_000,
    ) -> AsyncIterator[DriveEntry]:
        """The files below the folder, breadth first, folder by folder, each page as Graph
        returns it (200 entries). Folders deeper than ``max_depth`` (the folder itself is depth
        0) are skipped, and the iteration stops after ``max_items`` files."""
        async for entry in self._walk(recursive, max_depth, max_items, _Stats()):
            yield entry

    async def list_files(
        self,
        *,
        recursive: bool = True,
        max_depth: int = 10,
        max_items: int = 10_000,
    ) -> DriveListing:
        """Every file :meth:`iter_files` yields, and whether a limit stopped it."""
        stats = _Stats()
        files = [entry async for entry in self._walk(recursive, max_depth, max_items, stats)]
        return DriveListing(
            files=tuple(files),
            folders=stats.folders,
            truncated=stats.truncated,
            depth_limited=stats.depth_limited,
        )

    async def _walk(
        self, recursive: bool, max_depth: int, max_items: int, stats: _Stats
    ) -> AsyncIterator[DriveEntry]:
        if not 0 <= max_depth <= 50:
            raise ValueError("max_depth must be between 0 and 50")
        if not 1 <= max_items <= 100_000:
            raise ValueError("max_items must be between 1 and 100000")
        root = await self.root()
        queue: list[tuple[str, str, int]] = [(root.item_id, "", 0)]
        seen = {root.item_id}
        count = 0
        while queue:
            item_id, parent, depth = queue.pop(0)
            stats.folders += 1
            page: str | None = None
            while True:
                try:
                    entries, page = await self._children(root.drive_id, item_id, parent, page)
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    raise self._error("folder listing", exc) from exc
                for entry in entries:
                    if entry.is_folder:
                        if not recursive or entry.id in seen:
                            continue
                        if depth + 1 > max_depth:
                            stats.depth_limited = True
                            continue
                        seen.add(entry.id)
                        queue.append((entry.id, entry.path, depth + 1))
                        continue
                    if count >= max_items:
                        stats.truncated = True
                        return
                    count += 1
                    yield entry
                if page is None:
                    break

    async def list_folder(
        self, folder_id: str | None = None, *, page_token: str | None = None
    ) -> DriveFolderPage:
        """One page of a folder's direct children: the configured folder, or a folder below
        it by id. ``page_token`` continues a listing of the same folder."""
        root = await self.root()
        try:
            if folder_id is None or folder_id == root.item_id:
                item_id, parent = root.item_id, ""
            else:
                item = await self._item(folder_id)
                if "folder" not in item:
                    raise OneDriveError("that item is not a folder")
                item_id, parent = str(item["id"]), self._relative(item)
            page_url = None
            if page_token:
                token_folder, _, skip = page_token.partition("|")
                if token_folder != item_id or not skip or not re.fullmatch(r"[\w%=.+-]+", skip):
                    raise ValueError("page_token is not a page of this folder")
                page_url = self._http.url(
                    f"drives/{_segment(root.drive_id)}/items/{_segment(item_id)}/children"
                    f"?$select={_ITEM_FIELDS}&$top={_PAGE_SIZE}&$skiptoken={skip}"
                )
            entries, next_link = await self._children(root.drive_id, item_id, parent, page_url)
        except OneDriveError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise self._error("folder listing", exc) from exc
        token = None
        if next_link:
            next_skip = dict(parse_qsl(urlsplit(next_link).query)).get("$skiptoken")
            token = f"{item_id}|{quote(next_skip, safe='')}" if next_skip else None
        return DriveFolderPage(entries=tuple(entries), next_page_token=token)

    def _relative(self, item: Mapping[str, Any]) -> str:
        """The item's path relative to the configured folder; raises when it is outside it."""
        root = self._root
        assert root is not None
        parent = item.get("parentReference") or {}
        if str(parent.get("driveId") or "") != root.drive_id:
            raise OneDriveError("that item is not in the configured folder")
        tail = _tail(parent.get("path"))
        name = _name(item.get("name"))
        if tail is None:
            raise OneDriveError("that item is not in the configured folder")
        if tail == root.tail:
            return name
        if not tail.startswith(root.tail + "/"):
            raise OneDriveError("that item is not in the configured folder")
        return f"{tail[len(root.tail) + 1 :]}/{name}"

    async def _item(self, item_id: str) -> Mapping[str, Any]:
        if not _ID.fullmatch(item_id):
            raise ValueError("item id is invalid")
        root = await self.root()
        return await self._get(
            f"drives/{_segment(root.drive_id)}/items/{_segment(item_id)}",
            **{"$select": f"{_ITEM_FIELDS},@microsoft.graph.downloadUrl"},
        )

    async def get_file(self, item_id: str) -> DriveEntry:
        """One file's metadata, if it is below the configured folder."""
        try:
            item = await self._item(item_id)
            relative = self._relative(item)
        except OneDriveError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise self._error("file lookup", exc) from exc
        root = await self.root()
        parent = relative.rpartition("/")[0]
        return self._entry(item, root.drive_id, parent)

    async def download(self, item_id: str, *, max_bytes: int | None = None) -> AttachmentUpload:
        """One file below the configured folder, at most ``max_bytes`` (the settings'
        ``max_download_bytes`` by default), with its media type checked against its leading
        bytes. The content comes from Graph's pre-authenticated download URL, which must be on
        a configured download host."""
        limit = min(max_bytes or self._settings.max_download_bytes, 100_000_000)
        try:
            item = await self._item(item_id)
            self._relative(item)
            if "file" not in item:
                raise OneDriveError("that item is not a file")
            if int(item.get("size") or 0) > limit:
                raise OneDriveError("OneDrive file exceeds the configured size limit")
            url = item.get("@microsoft.graph.downloadUrl")
            if not url:
                root = await self.root()
                token = await self._oauth.token()
                redirect = await self._http.get(
                    self._http.url(
                        f"drives/{_segment(root.drive_id)}/items/{_segment(item_id)}/content"
                    ),
                    headers={"Authorization": f"Bearer {token}"},
                )
                if redirect.status_code not in (301, 302, 303, 307, 308):
                    redirect.raise_for_status()
                    raise ValueError("Graph did not redirect to the file's content")
                url = redirect.headers.get("location")
            response = await self._http.download_preauthenticated(
                str(url),
                host_suffixes=self._settings.download_host_suffixes,
                max_bytes=limit,
            )
            response.raise_for_status()
        except OneDriveError:
            raise
        except ResponseTooLarge as exc:
            raise OneDriveError("OneDrive file exceeds the configured size limit") from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise self._error("file download", exc) from exc
        name = _name(item.get("name")) or "file"
        declared = (item.get("file") or {}).get("mimeType")
        data = response.content
        return AttachmentUpload(
            filename=name,
            media_type=media_type_of(data, filename=name, declared=declared),
            data=data,
        )

    async def aclose(self) -> None:
        await self._http.aclose()


@dataclass
class _Stats:
    folders: int = 0
    truncated: bool = False
    depth_limited: bool = False
