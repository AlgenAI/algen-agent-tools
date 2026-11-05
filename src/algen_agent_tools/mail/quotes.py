"""Split a reply's own text from the history it quotes and its signature.

The rules are deterministic and conservative: text is removed only after a line that clearly
starts quoted history, so a reply never loses what its sender wrote.

- An attribution line: ``On <date>, <name> wrote:`` (Gmail, Apple Mail, Thunderbird; also
  wrapped over two lines, and in French, German, Spanish, Italian and Dutch, including
  ``Am <date> schrieb <name>:``).
- An Outlook header block: ``From:`` followed within four lines by ``Sent:`` or ``Date:`` and
  by ``To:`` or ``Subject:``, optionally after a ``____`` rule; or ``-----Original Message-----``.
- A run of ``>`` lines that nothing but blank lines and a signature follows. Quoted lines with
  answers between them (inline replies) are kept.
- A signature: the standard ``-- `` delimiter line, or a final ``Sent from my …`` line.

Forwarded messages (``---------- Forwarded message ----------``, ``Begin forwarded message:``)
are new content for the reader: nothing from such a line on is removed, and a message with one
keeps its signature. When the rules would leave nothing (a message that is only a quote), the
text is returned whole.
"""

from __future__ import annotations

import re

_ATTRIBUTION = re.compile(
    r"^\s*(?:On|Le|Am|El|Il|Op)\s.{1,300}?"
    r"(?:wrote|a écrit|schrieb|escribió|ha scritto|schreef)\s?:\s*$",
    re.I,
)
_ATTRIBUTION_VERB_FIRST = re.compile(r"^\s*Am\s.{1,200}?\sschrieb\s.{1,300}:\s*$", re.I)
_FORWARD = re.compile(
    r"^\s*(?:-{2,}\s*Forwarded message\s*-{2,}|Begin forwarded message:)\s*$", re.I
)
_ORIGINAL = re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I)
_RULE = re.compile(r"^\s*_{10,}\s*$")
_FROM = re.compile(r"^\s*\*?From:\*?\s", re.I)
_SENT = re.compile(r"^\s*\*?(?:Sent|Date):\*?\s", re.I)
_TO = re.compile(r"^\s*\*?(?:To|Subject):\*?\s", re.I)
_QUOTED = re.compile(r"^\s*>")
_SIGNATURE = re.compile(r"^-- ?$")
_SENT_FROM = re.compile(r"^\s*Sent from my \S.{0,60}$", re.I)


def _starts_history(lines: list[str], index: int) -> bool:
    line = lines[index]
    if _ATTRIBUTION.match(line) or _ATTRIBUTION_VERB_FIRST.match(line) or _ORIGINAL.match(line):
        return True
    if index + 1 < len(lines) and _ATTRIBUTION.match(f"{line} {lines[index + 1]}"):
        return line.strip().lower().startswith(("on ", "le ", "am ", "el ", "il ", "op "))
    start = index + 1 if _RULE.match(line) else index
    if start >= len(lines) or not _FROM.match(lines[start]):
        return False
    window = lines[start + 1 : start + 5]
    return any(_SENT.match(item) for item in window) and any(_TO.match(item) for item in window)


def _signature_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if _SIGNATURE.match(line):
            return index
    last = len(lines) - 1
    while last >= 0 and not lines[last].strip():
        last -= 1
    if last >= 0 and _SENT_FROM.match(lines[last]):
        return last
    return len(lines)


def latest_text(text: str) -> tuple[str, bool]:
    """The reply's own text without quoted history or signature, and whether anything was
    removed."""
    lines = text.split("\n")
    end = len(lines)
    forwarded = False
    for index in range(len(lines)):
        if _FORWARD.match(lines[index]):
            forwarded = True  # a forwarded message and everything after it is kept
            break
        if _starts_history(lines, index):
            end = index
            break
        if _QUOTED.match(lines[index]) and all(
            _QUOTED.match(line) or not line.strip() for line in lines[index:]
        ):
            end = index
            break
    kept = lines[:end]
    if not forwarded:
        kept = kept[: _signature_start(kept)]
    latest = "\n".join(kept).strip()
    if not latest:
        return text, False
    return latest, latest != text.strip()
