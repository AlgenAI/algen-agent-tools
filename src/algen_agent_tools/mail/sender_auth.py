"""Whether an inbound email really came from the address in its From line.

The receiving mail server checks SPF, DKIM and DMARC on arrival and records the results in an
``Authentication-Results`` header it adds at the top of the message (RFC 8601). Only that topmost
header, and only when it names the server the mailbox trusts, is read: anyone can add such a
header lower down. Mail read without that header is ``unknown``, never ``fail``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

SenderAuth = Literal["pass", "fail", "unknown"]
GMAIL_AUTHSERV_ID = "mx.google.com"

# Each result is "method=result" at the start of a ";"-separated part. Comments in parentheses
# and the properties after the result (smtp.mailfrom=..., header.from=...) hold text the sender
# chose, so they are removed or ignored: only the leading token of each part counts.
_COMMENT = re.compile(r"\([^()]*\)")
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_RESULT = re.compile(r"^\s*(spf|dkim|dmarc)\s*=\s*([a-z]+)\b", re.IGNORECASE)


def authentication_verdict(header: str | None, trusted_server: str) -> SenderAuth:
    """``pass`` / ``fail`` / ``unknown`` from one Authentication-Results header value.

    DMARC decides when present (a failure wins over a pass). Without DMARC, an SPF or DKIM pass
    may be for any domain, so only an SPF failure without a DKIM pass counts as ``fail``.
    """
    if not header:
        return "unknown"
    server = header.split(";", 1)[0].strip().casefold()
    if server != trusted_server.strip().casefold():
        return "unknown"
    text = _QUOTED.sub(" ", header)  # a quoted address may hold ";" and "dmarc=pass"
    while _COMMENT.search(text):  # nested comments, innermost first
        text = _COMMENT.sub(" ", text)
    results: dict[str, list[str]] = {}
    for part in text.split(";")[1:]:
        found = _RESULT.match(part)
        if found:
            results.setdefault(found.group(1).casefold(), []).append(found.group(2).casefold())
    dmarc = results.get("dmarc", [])
    if dmarc:
        return "fail" if "fail" in dmarc else "pass" if "pass" in dmarc else "unknown"
    if "fail" in results.get("spf", []) and "pass" not in results.get("dkim", []):
        return "fail"
    return "unknown"


def topmost_verdict(headers: Sequence[str], trusted_server: str) -> SenderAuth:
    """The verdict of the first (topmost) Authentication-Results header in message order."""
    return authentication_verdict(headers[0], trusted_server) if headers else "unknown"
