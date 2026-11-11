"""Links behind mail-client redirects, and links to named hosts found before redaction."""

from __future__ import annotations

import base64
from urllib.parse import quote

import pytest

from algen_agent_tools.documents import find_links as documents_find_links
from algen_agent_tools.documents.inspection import find_links, redact_pii, unwrap_redirect
from algen_agent_tools.mail import find_links as mail_find_links
from algen_agent_tools.mail import unwrap_redirect as mail_unwrap_redirect

REAL = "https://github.com/octocat/hello-world"


def gmail(url: str) -> str:
    return f"https://www.google.com/url?q={quote(url, safe='')}&sa=D&source=editors&ust=1"


def safelink(url: str) -> str:
    return (
        f"https://eur01.safelinks.protection.outlook.com/?url={quote(url, safe='')}"
        "&data=05%7C02%7Cperson%40example.com&sdata=abc%3D&reserved=0"
    )


def proofpoint_v2(url: str) -> str:
    # Proofpoint percent-encodes "-" and "_" too, then writes "%" as "-" and "/" as "_".
    encoded = quote(url, safe="/").replace("-", "%2D").replace("_", "%5F")
    encoded = encoded.replace("%", "-").replace("/", "_")
    return (
        f"https://urldefense.proofpoint.com/v2/url?u={encoded}&d=DwMFaQ&c=abc&r=def&m=ghi&s=jkl&e="
    )


def test_the_helpers_are_exported_from_documents_and_mail() -> None:
    assert documents_find_links is find_links is mail_find_links
    assert mail_unwrap_redirect is unwrap_redirect


@pytest.mark.parametrize(
    "wrapped",
    [
        gmail(REAL),
        "https://www.google.co.in/url?q=https://github.com/octocat/hello-world&sa=D",
        "https://google.com/url?sa=t&url=https%3A%2F%2Fgithub.com%2Foctocat%2Fhello-world",
        safelink(REAL),
        "https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fgithub.com%2Foctocat"
        "%2Fhello-world&amp;data=1",
        proofpoint_v2(REAL),
        "https://urldefense.proofpoint.com/v1/url?u=https%3A%2F%2Fgithub.com%2Foctocat%2Fhello-world&k=x",
        "https://urldefense.com/v3/__https://github.com/octocat/hello-world__;!!G2kpM7uM-TzIFchu!abc$",
    ],
)
def test_each_wrapper_style_is_unwrapped(wrapped: str) -> None:
    assert unwrap_redirect(wrapped) == REAL


def test_proofpoint_v3_restores_the_characters_it_replaced() -> None:
    replaced = base64.urlsafe_b64encode(b"&&").decode().rstrip("=")
    wrapped = f"https://urldefense.com/v3/__https://example.com/a?x=1*y=2*z=3__;{replaced}!!abc$"
    assert unwrap_redirect(wrapped) == "https://example.com/a?x=1&y=2&z=3"
    run = base64.urlsafe_b64encode(b"%%%").decode()
    wrapped = f"https://urldefense.com/v3/__https://example.com/a**Bb__;{run}!!abc$"
    assert unwrap_redirect(wrapped) == "https://example.com/a%%%b"


def test_nested_wrappers_are_unwrapped_up_to_the_depth() -> None:
    nested = safelink(gmail(proofpoint_v2(REAL)))
    assert unwrap_redirect(nested) == REAL
    assert unwrap_redirect(nested, max_depth=1) == gmail(proofpoint_v2(REAL))
    assert unwrap_redirect(nested, max_depth=0) == nested
    deep = REAL
    for _ in range(10):
        deep = gmail(deep)
    assert unwrap_redirect(deep).startswith("https://www.google.com/url?q=")
    with pytest.raises(ValueError, match="max_depth"):
        unwrap_redirect(REAL, max_depth=-1)


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/octocat",
        "https://www.google.com/search?q=https://github.com",
        "https://www.google.com/url?sa=D",
        "https://www.google.com/url?q=javascript:alert(1)",
        "https://eur01.safelinks.protection.outlook.com/?url=mailto%3Aa%40example.com",
        "https://safelinks.protection.outlook.com.evil.example/?url=https%3A%2F%2Fgithub.com",
        "https://urldefense.com/v3/__https://example.com/*__;!!abc$",
        "https://urldefense.com/v3/__https://example.com/*__;%%%!!abc$",
        "https://urldefense.com/v3/no-markers",
        "https://[::1/url?q=https://github.com",
        "not a link",
        "",
    ],
)
def test_unwrapped_or_malformed_links_come_back_unchanged(link: str) -> None:
    assert unwrap_redirect(link) == link


def test_find_links_unwraps_tidies_and_keeps_order() -> None:
    text = (
        f"My profile: {safelink('https://www.linkedin.com/in/asha-rao/')} and gitlab.com/asha. "
        f"Code at {gmail(REAL)}. Site: https://example.com/about, "
        "and again https://GitHub.com/Octocat/Hello-World/."
    )
    assert find_links(text, hosts=["github.com", "linkedin.com", "gitlab.com"]) == [
        "linkedin.com/in/asha-rao",
        "gitlab.com/asha",
        "github.com/octocat/hello-world",
    ]


def test_find_links_never_returns_a_query_string_fragment_or_credentials() -> None:
    text = (
        "Reset at https://user:pass@app.example.com:8443/reset?token=s3cr3t#step "
        f"or {gmail('https://app.example.com/invite?code=abc123&x=1')}"
    )
    links = find_links(text, hosts=["example.com"])
    assert links == ["app.example.com/reset", "app.example.com/invite"]
    for secret in ("s3cr3t", "abc123", "pass", "8443", "#", "?"):
        assert all(secret not in link for link in links)


def test_find_links_matches_a_host_and_its_subdomains_only() -> None:
    text = (
        "gist.github.com/a https://www.github.com/b github.com.evil.example/c "
        "https://evil-github.com/d notgithub.com/e x.evil-github.com/f me@github.com/g "
        "https://github.community/h"
    )
    assert find_links(text, hosts=["GitHub.com"]) == ["gist.github.com/a", "github.com/b"]


def test_a_bare_host_needs_a_path_but_a_full_url_does_not() -> None:
    assert find_links("I use github.com daily", hosts=["github.com"]) == []
    assert find_links("See https://github.com", hosts=["github.com"]) == ["github.com"]
    assert find_links("See www.github.com/x.", hosts=["github.com"]) == ["github.com/x"]


def test_find_links_strips_trailing_punctuation_and_markup() -> None:
    text = "(https://github.com/a), [github.com/b](https://github.com/c) <https://github.com/d>!"
    assert find_links(text, hosts=["github.com"]) == [
        "github.com/a",
        "github.com/b",
        "github.com/c",
        "github.com/d",
    ]


def test_find_links_is_capped() -> None:
    text = " ".join(f"https://github.com/user{index}" for index in range(50))
    assert len(find_links(text, hosts=["github.com"])) == 20
    assert find_links(text, hosts=["github.com"], limit=2) == [
        "github.com/user0",
        "github.com/user1",
    ]
    assert find_links(text, hosts=["github.com"], limit=0) == []
    long = "https://github.com/" + "a" * 500
    assert len(find_links(long, hosts=["github.com"])[0]) == 200
    with pytest.raises(ValueError, match="limit"):
        find_links(text, hosts=["github.com"], limit=-1)


def test_find_links_with_no_hosts_finds_nothing_and_accepts_one_host_as_a_string() -> None:
    assert find_links(f"{REAL}", hosts=[]) == []
    assert find_links(f"{REAL}", hosts=["", " . "]) == []
    assert find_links(f"{REAL}", hosts="github.com") == ["github.com/octocat/hello-world"]


def test_find_links_before_redaction_tells_which_hosts_were_linked() -> None:
    body = f"Hi, my work is at {safelink(REAL)}. Call +1 555 123 4567."
    links = find_links(body, hosts=["github.com", "linkedin.com"])
    links_given = sorted({link.partition("/")[0] for link in links})
    redacted = redact_pii(body)
    assert links_given == ["github.com"]
    assert "octocat" not in redacted and "[URL REDACTED]" in redacted
