"""The receiving server's verdict on an inbound sender (SPF, DKIM and DMARC)."""

from __future__ import annotations

import pytest

from algen_agent_tools.mail import (
    InboundEmailSummary,
    authentication_verdict,
    topmost_verdict,
)

GOOGLE = "mx.google.com"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (
            f"{GOOGLE}; dkim=pass header.i=@acme.com; spf=pass; dmarc=pass (p=NONE) header.from=acme.com",
            "pass",
        ),
        (
            f"{GOOGLE}; spf=fail smtp.mailfrom=acme.com; dmarc=fail (p=REJECT) header.from=acme.com",
            "fail",
        ),
        (f"{GOOGLE}; dmarc=pass header.from=a.com; dmarc=fail header.from=b.com", "fail"),
        (f"{GOOGLE}; spf=fail smtp.mailfrom=x.com", "fail"),
        (f"{GOOGLE}; spf=fail smtp.mailfrom=x.com; dkim=pass header.i=@x.com", "unknown"),
        (f"{GOOGLE}; spf=pass smtp.mailfrom=anything.com", "unknown"),
        (f"{GOOGLE}; dmarc=none", "unknown"),
    ],
)
def test_verdict_follows_dmarc_then_spf_failures(header: str, expected: str) -> None:
    assert authentication_verdict(header, GOOGLE) == expected


def test_only_the_trusted_server_is_believed() -> None:
    assert authentication_verdict("evil.example; dmarc=pass", GOOGLE) == "unknown"
    assert authentication_verdict(f" {GOOGLE.upper()} ; dmarc=pass", GOOGLE) == "pass"
    assert authentication_verdict("", GOOGLE) == "unknown"
    assert authentication_verdict(None, GOOGLE) == "unknown"


def test_sender_chosen_text_cannot_fake_a_result() -> None:
    quoted = f'{GOOGLE}; spf=fail smtp.mailfrom="x;dmarc=pass"@evil.com'
    commented = f"{GOOGLE}; spf=fail (sender said: ; dmarc=pass (really)) smtp.mailfrom=evil.com"
    assert authentication_verdict(quoted, GOOGLE) == "fail"
    assert authentication_verdict(commented, GOOGLE) == "fail"


def test_only_the_topmost_header_counts() -> None:
    forged_below = [f"{GOOGLE}; dmarc=fail header.from=bank.com", f"{GOOGLE}; dmarc=pass"]
    assert topmost_verdict(forged_below, GOOGLE) == "fail"
    assert topmost_verdict([], GOOGLE) == "unknown"


def test_summaries_default_to_unknown() -> None:
    assert InboundEmailSummary(provider_message_id="m").sender_auth == "unknown"
