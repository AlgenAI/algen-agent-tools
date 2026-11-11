"""Links in untrusted text: the real address behind a mail client's redirect, and the links to
hosts a caller names, found before :func:`redact_pii` removes every URL.

Nothing here touches the network: a redirect is unwrapped from the address itself, and a link
that cannot be unwrapped is returned as it is. :func:`find_links` returns ``host/path`` only, so
query strings and fragments, which can carry tokens, never come back.
"""

from __future__ import annotations

import base64
import binascii
import re
import string
from collections.abc import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

MAX_UNWRAP_DEPTH = 5
"""How many redirect wrappers :func:`unwrap_redirect` follows (Safe Links around Gmail, ...)."""
MAX_LINKS = 20
MAX_LINK_LENGTH = 200

# Gmail and Google Groups wrap links as google.<tld>/url?q=<real> (or ?url=).
_GOOGLE = re.compile(r"^(?:www\.)?google\.(?:com|co)(?:\.[a-z]{2})?$|^(?:www\.)?google\.[a-z]{2}$")
_SAFELINKS = "safelinks.protection.outlook.com"
_PROOFPOINT_V2 = re.compile(r"^/v[12]/url$")
_PROOFPOINT_V3 = re.compile(r"/v3/__(?P<url>.+?)__;(?P<bytes>[A-Za-z0-9_=-]*)(?:!|$)")
_V3_TOKEN = re.compile(r"\*(\*.)?")
_V3_RUNS = {
    character: length
    for length, character in enumerate(
        string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_", start=2
    )
}
_WEB = re.compile(r"^(?:https?://|www\.)", re.I)
_URL = r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+"
_TRAILING = ".,;:!?'\"*"


def _split(link: str) -> tuple[str, str, str]:
    """``(host, path, query)`` of a web link, with or without its scheme."""
    try:
        parts = urlsplit(link if "://" in link else f"https://{link}")
        host = (parts.hostname or "").casefold()
    except ValueError:  # a malformed address, such as an unclosed IPv6 bracket
        return "", "", ""
    return host, parts.path, parts.query


def _parameter(query: str, keys: tuple[str, ...]) -> str | None:
    # A link copied from HTML can keep its escaped ampersands.
    values = parse_qs(query.replace("&amp;", "&"))
    return next((values[key][0] for key in keys if values.get(key)), None)


def _proofpoint_v3(link: str) -> str | None:
    match = _PROOFPOINT_V3.search(link)
    if match is None:
        return None
    try:
        replacements = base64.urlsafe_b64decode(match.group("bytes") + "==").decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    encoded = unquote(match.group("url"))
    out: list[str] = []
    position = 0
    marker = 0
    for token in _V3_TOKEN.finditer(encoded):
        out.append(encoded[position : token.start()])
        run = 1 if token.group(1) is None else _V3_RUNS.get(token.group(1)[-1])
        if run is None or marker + run > len(replacements):
            return None
        out.append(replacements[marker : marker + run])
        marker += run
        position = token.end()
    out.append(encoded[position:])
    return "".join(out)


def _unwrap_once(link: str) -> str | None:
    host, path, query = _split(link)
    if _GOOGLE.match(host) and path == "/url":
        return _parameter(query, ("q", "url"))
    if host == _SAFELINKS or host.endswith(f".{_SAFELINKS}"):
        return _parameter(query, ("url",))
    if host == "urldefense.proofpoint.com" and _PROOFPOINT_V2.match(path):
        target = _parameter(query, ("u",))
        if target is None:
            return None
        if path.startswith("/v1"):
            return target
        # v2 writes "%" as "-" and "/" as "_" before percent-encoding the rest.
        return unquote(target.replace("-", "%").replace("_", "/"))
    if host in {"urldefense.com", "urldefense.proofpoint.com"} and path.startswith("/v3/__"):
        return _proofpoint_v3(link)
    return None


def unwrap_redirect(url: str, *, max_depth: int = MAX_UNWRAP_DEPTH) -> str:
    """The link behind a mail client's redirect wrapper, without visiting either.

    Unwraps Gmail (``google.<tld>/url?q=``), Outlook Safe Links
    (``*.safelinks.protection.outlook.com/?url=``) and Proofpoint URL Defense (v1, v2 and v3),
    including one wrapped in another, up to ``max_depth`` wrappers. A link that is not wrapped,
    is malformed, or whose target is not a web address (``javascript:``, ``mailto:``) is
    returned unchanged, or as far as it could be unwrapped.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be 0 or more")
    link = url.strip()
    for _ in range(max_depth):
        target = _unwrap_once(link)
        if target is None:
            break
        target = target.strip()
        if not _WEB.match(target):
            break
        link = target
    return link


def _normalize_host(host: str) -> str:
    return host.strip().casefold().strip(".").removeprefix("www.")


def find_links(
    text: str,
    *,
    hosts: Iterable[str],
    limit: int = MAX_LINKS,
) -> list[str]:
    """Links in ``text`` to one of ``hosts`` or a subdomain of one, as ``host/path``.

    Each link is unwrapped (:func:`unwrap_redirect`) before its host is checked, so a GitHub
    link inside an Outlook Safe Link is found. Links with a scheme or ``www.`` are found
    anywhere; a bare ``host/path`` (``github.com/octocat``) only for the given hosts. Results
    are tidied (lower-case host without ``www.``, no port, credentials, query string or
    fragment, no trailing slash, at most 200 characters), de-duplicated ignoring case and kept
    in the order they appear, at most ``limit`` of them.

    Call it before :func:`redact_pii`, which replaces every URL: a model can then be told which
    hosts the sender linked to without seeing the links. Paths are returned as written; a host
    whose paths carry secrets (one-time links) is not one to ask for.
    """
    if limit < 0:
        raise ValueError("limit must be 0 or more")
    wanted = tuple(
        dict.fromkeys(
            host
            for host in map(_normalize_host, (hosts,) if isinstance(hosts, str) else hosts)
            if host
        )
    )
    if not wanted or limit == 0:
        return []
    bare = "|".join(re.escape(host) for host in wanted)
    pattern = re.compile(
        rf"{_URL}|(?<![\w@.-])(?:[a-z0-9-]+\.)*(?:{bare})/[^\s<>\"'()\[\]{{}}]+", re.I
    )
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        link = unwrap_redirect(match.group(0).rstrip(_TRAILING))
        host, path, _ = _split(link)
        host = host.removeprefix("www.")
        if not any(host == item or host.endswith(f".{item}") for item in wanted):
            continue
        tidy = f"{host}{path.rstrip('/')}"[:MAX_LINK_LENGTH]
        key = tidy.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(tidy)
        if len(found) >= limit:
            break
    return found
