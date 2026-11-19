from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.onedrive.toolkit import (
    CERTIFICATE,
    CERTIFICATE_THUMBPRINT,
    CLIENT_ID,
    CLIENT_SECRET,
    FOLDER,
    PRIVATE_KEY,
    PRIVATE_KEY_PASSPHRASE,
    TENANT_ID,
    onedrive_toolkit,
)

INFO = ConnectorInfo(
    connector="onedrive",
    title="OneDrive and SharePoint files",
    summary=(
        "List and read the files below one OneDrive or SharePoint folder through Microsoft "
        "Graph, read-only, with the app's own credentials (client secret or certificate)."
    ),
    category="documents",
    extra="onedrive",
    settings=(
        Setting(
            env=env_name(TENANT_ID),
            description="The Microsoft Entra tenant id or domain.",
            example="contoso.onmicrosoft.com",
        ),
        Setting(env=env_name(CLIENT_ID), description="The app registration's client id."),
        Setting(
            env=env_name(FOLDER),
            description=(
                "The folder: a sharing link, or a Graph path such as "
                "drives/{drive-id}/items/{item-id} or users/{upn}/drive/root:/{path}."
            ),
            example="users/hr@contoso.com/drive/root:/Careers",
        ),
        Setting(
            env=env_name(CLIENT_SECRET),
            description=(
                "The app registration's client secret. Unset when the app authenticates with a "
                "certificate (ALGEN_TOOLS_ONEDRIVE_PRIVATE_KEY)."
            ),
            secret=True,
            required=False,
        ),
        Setting(
            env=env_name(PRIVATE_KEY),
            description=(
                "The certificate's PEM private key (RSA), for certificate authentication. A "
                "single line with \\n escapes is accepted."
            ),
            secret=True,
            required=False,
        ),
        Setting(
            env=env_name(PRIVATE_KEY_PASSPHRASE),
            description="The passphrase of an encrypted private key.",
            secret=True,
            required=False,
        ),
        Setting(
            env=env_name(CERTIFICATE),
            description="The PEM certificate uploaded to the app registration (preferred).",
            required=False,
        ),
        Setting(
            env=env_name(CERTIFICATE_THUMBPRINT),
            description="The certificate's SHA-1 thumbprint, when the certificate is not set.",
            required=False,
            example="3F2504E04F8911D39A0C0305E82C3301F2504E04",
        ),
    ),
    egress=(
        Egress(protocol="https", host="login.microsoftonline.com", port=443),
        Egress(protocol="https", host="graph.microsoft.com", port=443),
        Egress(protocol="https", host="*.sharepoint.com", port=443),
    ),
    code_only_options=("artifact_sink", "attachment_policy", "documents"),
)

OnedrivePlugin = toolkit_plugin(INFO, onedrive_toolkit)
