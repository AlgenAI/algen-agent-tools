# OneDrive and SharePoint files

List and read the files below one OneDrive or SharePoint folder through Microsoft Graph,
read-only, with an Entra app registration's own credentials (application access, no user).

| | |
| --- | --- |
| Plugin | `algen_agent_tools.onedrive` |
| Extra | `pip install "algen-agent-tools[onedrive]"` (`cryptography`, for certificate authentication) |
| Environment | `ALGEN_TOOLS_ONEDRIVE_TENANT_ID`, `ALGEN_TOOLS_ONEDRIVE_CLIENT_ID`, `ALGEN_TOOLS_ONEDRIVE_FOLDER`; a client secret (`ALGEN_TOOLS_ONEDRIVE_CLIENT_SECRET`) or a certificate (`ALGEN_TOOLS_ONEDRIVE_PRIVATE_KEY`, optional `ALGEN_TOOLS_ONEDRIVE_PRIVATE_KEY_PASSPHRASE`, and `ALGEN_TOOLS_ONEDRIVE_CERTIFICATE` or `ALGEN_TOOLS_ONEDRIVE_CERTIFICATE_THUMBPRINT`) |
| Since | 0.2.0a14 |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `onedrive.list_folder` | read | `onedrive.read` | 60 s | Runtime default | agent policy |
| `onedrive.get_file` (with `artifact_sink`) | read | `onedrive.read` | 120 s | Runtime default | agent policy |
| `onedrive.read_file` (with `documents`) | read | `onedrive.read` | 120 s | Runtime default | agent policy |

- `onedrive.list_folder(folder_id=None, page_token=None)` lists the files and folders directly
  in the configured folder, or in a folder below it by id: `id`, `name`, `path` (relative to the
  configured folder), `is_folder`, `size`, `mime_type`, `last_modified`, `etag`, `ctag`,
  `web_url`. Pages hold up to 200 entries; `next_page_token` is opaque (the folder id and Graph's
  skip token, never a URL). It is `volatile`: a folder changes on its own.
- `onedrive.get_file(file_id)` saves one file as an artifact and returns its reference, after the
  `attachment_policy` check (size, allowed types, leading bytes that match the type).
- `onedrive.read_file(file_id, max_chars=50000)` returns the file's inspected text, as the mail
  connectors' `read_attachment` does; the file never reaches the model.
- An item outside the configured folder (another folder of the same drive, another drive) is
  refused, whatever id the model passes.

There are no write tools, and the client has no write code path: every Graph request is a
`GET` (the token request is the only `POST`).

## Naming the folder

`ALGEN_TOOLS_ONEDRIVE_FOLDER` (or `FolderRef.parse` in code) takes one of:

| Form | Example |
| --- | --- |
| Drive and folder id (works with every grant below) | `drives/b!Xyz.../items/01ABCDEF...` |
| A path in a drive | `drives/b!Xyz.../root:/Careers/Resumes` |
| A path in a user's OneDrive | `users/hr@contoso.com/drive/root:/Careers` |
| A path in a site's default library | `sites/contoso.sharepoint.com:/sites/HR:/drive/root:/CVs`, or `sites/{site-id}/drive/root:/CVs` |
| A sharing link to the folder | `https://contoso.sharepoint.com/:f:/s/HR/Eab...` |

Paths are folder paths: `.`, `..`, `:` and backslashes are refused. In code, `FolderRef(...)`
takes the same parts as fields.

## What IT grants (least privilege)

Do not grant `Files.Read.All` or `Sites.Read.All`: they reach every file in the tenant. Use a
*Selected* permission, which gives the app no access at all until an administrator grants it one
site, library or folder ([Microsoft: selected permissions in OneDrive and
SharePoint](https://learn.microsoft.com/graph/permissions-selected-overview)). Both steps are
needed:

1. **Consent the application permission** in Entra ID (App registrations → API permissions →
   Microsoft Graph → Application permissions), then *Grant admin consent*. Choose one:
   - `Sites.Selected`: the app is granted one **site collection**. A user's OneDrive is a site
     (`https://contoso-my.sharepoint.com/personal/hr_contoso_com`), so this covers a OneDrive
     folder as well as a SharePoint library. Every folder form above works.
   - `Files.SelectedOperations.Selected`: the app is granted one **folder or file** in a document
     library (the grant breaks permission inheritance on that folder). Name the folder by drive
     and folder id (`drives/{drive-id}/items/{item-id}`): resolving a path or a sharing link may
     need access above the folder, which this grant does not give.
2. **Grant the app the `read` role on the resource** with Microsoft Graph, as an administrator
   with `Sites.FullControl.All` (for a site) or full control of the site (for a folder), for
   example in Graph Explorer or with the Microsoft Graph PowerShell SDK. `{app-client-id}` is the
   app registration's client id.

   Site (with `Sites.Selected`):

   ```http
   POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
   Content-Type: application/json

   {"roles": ["read"],
    "grantedToIdentities": [{"application": {"id": "{app-client-id}", "displayName": "Careers-Mailbox-Reader"}}]}
   ```

   Folder (with `Files.SelectedOperations.Selected`):

   ```http
   POST https://graph.microsoft.com/v1.0/drives/{drive-id}/items/{folder-item-id}/permissions
   Content-Type: application/json

   {"roles": ["read"], "grantedToV2": {"application": {"id": "{app-client-id}"}}}
   ```

   PowerShell: `New-MgSitePermission -SiteId $siteId -BodyParameter $params` or
   `New-MgDriveItemPermission -DriveId $driveId -DriveItemId $folderId -BodyParameter $params`.
   Find the site id with `GET /sites/contoso-my.sharepoint.com:/personal/hr_contoso_com` (or
   `/sites/contoso.sharepoint.com:/sites/HR`), the drive id with `GET /sites/{site-id}/drive`
   and the folder id with `GET /drives/{drive-id}/root:/Careers`.

`read` lets the app read metadata and content and nothing else. Revoke access by deleting the
permission (`DELETE /sites/{site-id}/permissions/{id}`) or by removing the consent.

**Sharing links.** Microsoft lists `Files.ReadWrite.All` as the least-privileged application
permission for resolving a sharing link (`GET /shares/{id}`). Whether a Selected grant is enough
to resolve a link is not documented, so prefer the drive and folder id (or a path with
`Sites.Selected`), which never needs more than `read`.

## Authentication

The same app credentials as [Outlook](outlook.md#certificate-authentication): a client secret,
or a certificate (`GraphCertificate`). Certificate authentication sends a signed client
assertion (RS256, `x5t#S256` from the certificate, or `x5t` from a SHA-1 thumbprint); the key
never leaves the process.

## Guarantees

- Requests go to `login.microsoftonline.com` and `graph.microsoft.com`; file content comes from
  Graph's pre-authenticated download URL (`@microsoft.graph.downloadUrl`, or the `/content`
  redirect), followed only when it is `https` on port 443 on a host ending in one of
  `download_host_suffixes` (`.sharepoint.com` by default; add `.sharepoint.us` or
  `.sharepoint.cn` for a national cloud). No `Authorization` header is sent there, redirects are
  not followed further, and the URL's query (a short-lived grant) is redacted from `httpx` logs.
- Downloads stop at `max_download_bytes` (20 MB by default, at most 100 MB), checked against the
  size Graph declares and again while reading. The media type comes from the content's leading
  bytes when they have a known signature (PDF, images, Office files), as the attachments check
  reads them, else from Graph's type or the file name.
- Listing pages follow only Graph's `@odata.nextLink` on the configured API base.
- Throttling (429, 503) raises a `OneDriveError` that is also an
  `algen_agent_tools.msgraph.GraphThrottled` with `retry_after_seconds` from Graph's
  `Retry-After`. The connector never retries; a batch job waits and continues.
- Errors name the operation and the cause's type, never Graph's response, a secret or a file's
  content.

## In code

```python
from algen_agent_tools.msgraph import GraphCertificate
from algen_agent_tools.onedrive import FolderRef, OneDriveFilesClient, OneDriveSettings

client = OneDriveFilesClient(
    OneDriveSettings(tenant_id=tenant, client_id=app_id, certificate=GraphCertificate(
        private_key=SecretStr(pem_key), certificate=pem_certificate)),
    FolderRef.parse("users/hr@contoso.com/drive/root:/Careers"),
)
async for entry in client.iter_files(max_depth=10, max_items=10_000):
    upload = await client.download(entry.id)   # AttachmentUpload(filename, media_type, data)
```

`list_files(recursive=True, max_depth=10, max_items=10000)` returns the same files as a
`DriveListing` with `truncated` (more than `max_items`) and `depth_limited` (folders deeper than
`max_depth` skipped). `get_file(id)` returns one entry's metadata. Each entry's `ctag` changes
when the content changes, its `etag` also on a rename: use them to skip unchanged files.
