"""Build small Outlook .msg files for tests: a minimal OLE compound-file writer and the MAPI
property streams Outlook stores."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SECTOR = 512
MINI = 64
CUTOFF = 4096
FREE, END, FAT, NONE = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


@dataclass
class _Entry:
    name: str
    kind: int  # 1 storage, 2 stream, 5 root
    data: bytes = b""
    children: list[_Entry] = field(default_factory=list)


def compound_file(streams: dict[str, bytes]) -> bytes:
    """An OLE compound file (version 3) holding ``streams`` by ``a/b/c`` path. Streams under
    4096 bytes live in the mini stream, as the format requires."""
    root = _Entry("Root Entry", 5)
    for path, data in streams.items():
        node = root
        *storages, name = path.split("/")
        for part in storages:
            found = next((child for child in node.children if child.name == part), None)
            if found is None:
                found = _Entry(part, 1)
                node.children.append(found)
            node = found
        node.children.append(_Entry(name, 2, data))
    entries: list[_Entry] = []

    def walk(entry: _Entry) -> None:
        entries.append(entry)
        for child in entry.children:
            walk(child)

    walk(root)
    index = {id(entry): position for position, entry in enumerate(entries)}
    sectors: list[bytes] = []
    starts: dict[int, int] = {}
    chains: list[tuple[int, int]] = []

    def place(data: bytes) -> int:
        start, count = len(sectors), -(-len(data) // SECTOR)
        padded = data.ljust(count * SECTOR, b"\0")
        chains.append((start, count))
        sectors.extend(padded[i : i + SECTOR] for i in range(0, len(padded), SECTOR))
        return start

    mini = bytearray()
    mini_chains: list[tuple[int, int]] = []
    for entry in entries:
        if entry.kind == 2 and len(entry.data) >= CUTOFF:
            starts[id(entry)] = place(entry.data)
        elif entry.kind == 2 and entry.data:
            start, count = len(mini) // MINI, -(-len(entry.data) // MINI)
            starts[id(entry)] = start
            mini_chains.append((start, count))
            mini += entry.data.ljust(count * MINI, b"\0")
    if mini:
        starts[id(root)] = place(bytes(mini))
        mini_table = [FREE] * (-(-len(mini) // MINI // (SECTOR // 4)) * (SECTOR // 4))
        for start, count in mini_chains:
            for offset in range(count):
                mini_table[start + offset] = start + offset + 1 if offset < count - 1 else END
        first_minifat = place(struct.pack(f"<{len(mini_table)}I", *mini_table))
        minifat_count = len(mini_table) // (SECTOR // 4)
    else:
        first_minifat, minifat_count = END, 0
    directory = b""
    for entry in entries:
        name = entry.name.encode("utf-16-le") + b"\0\0"
        child = index[id(entry.children[0])] if entry.children else NONE
        directory += struct.pack(
            "<64sHBBIII16sIQQIQ",
            name,
            len(name),
            entry.kind,
            1,
            NONE,
            NONE,
            child,
            b"\0" * 16,
            0,
            0,
            0,
            starts.get(id(entry), END),
            len(mini) if entry.kind == 5 else len(entry.data),
        )
    # Each storage's children hang off its first child through right-sibling links.
    raw = bytearray(directory)
    for entry in entries:
        for left, right in zip(entry.children, entry.children[1:], strict=False):
            offset = index[id(left)] * 128 + 72
            raw[offset : offset + 4] = struct.pack("<I", index[id(right)])
    directory = bytes(raw).ljust(-(-len(raw) // SECTOR) * SECTOR, b"\0")
    first_directory = len(sectors)
    directory_count = len(directory) // SECTOR
    chains.append((first_directory, directory_count))
    sectors.extend(directory[i : i + SECTOR] for i in range(0, len(directory), SECTOR))
    fat_count = 1
    while len(sectors) + fat_count > fat_count * (SECTOR // 4):
        fat_count += 1
    first_fat = len(sectors)
    table = [FREE] * (fat_count * (SECTOR // 4))
    for start, count in chains:
        for offset in range(count):
            table[start + offset] = start + offset + 1 if offset < count - 1 else END
    for offset in range(fat_count):
        table[first_fat + offset] = FAT
    fat = struct.pack(f"<{len(table)}I", *table)
    sectors.extend(fat[i : i + SECTOR] for i in range(0, len(fat), SECTOR))
    difat = [first_fat + offset for offset in range(fat_count)] + [FREE] * (109 - fat_count)
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII",
        bytes.fromhex("D0CF11E0A1B11AE1"),
        b"\0" * 16,
        0x3E,
        3,
        0xFFFE,
        9,
        6,
        b"\0" * 6,
        0,
        fat_count,
        first_directory,
        0,
        CUTOFF,
        first_minifat,
        minifat_count,
        END,
        0,
    ) + struct.pack("<109I", *difat)
    return header + b"".join(sectors)


def _unicode(value: str) -> bytes:
    return value.encode("utf-16-le")


def _filetime(moment: datetime) -> bytes:
    epoch = datetime(1601, 1, 1, tzinfo=UTC)
    return struct.pack("<Q", int((moment - epoch).total_seconds() * 10_000_000))


def _properties(header: int, values: dict[tuple[int, int], bytes]) -> bytes:
    body = b"".join(
        struct.pack("<II", (property_id << 16) | kind, 0) + value.ljust(8, b"\0")[:8]
        for (property_id, kind), value in values.items()
    )
    return b"\0" * header + body


def message_streams(spec: dict[str, Any], prefix: str = "", header: int = 32) -> dict[str, bytes]:
    """The streams of one message from ``spec``: subject, body, html, rtf, sender_name,
    sender_email, date (a datetime), headers (transport headers), recipients
    ``[(name, address, 1 for To or 2 for Cc)]`` and attachments ``[{"name", "mime", "data"}]``
    or ``[{"name", "message": spec}]``."""
    streams: dict[str, bytes] = {}

    def text(property_id: str, value: str, where: str = prefix) -> None:
        streams[f"{where}__substg1.0_{property_id}001F"] = _unicode(value)

    fixed: dict[tuple[int, int], bytes] = {}
    for key, property_id in (
        ("subject", "0037"),
        ("body", "1000"),
        ("sender_name", "0C1A"),
        ("sender_email", "5D01"),
        ("headers", "007D"),
        ("display_to", "0E04"),
    ):
        if key in spec:
            text(property_id, spec[key])
    if "html" in spec:
        streams[f"{prefix}__substg1.0_10130102"] = spec["html"].encode()
    if "rtf" in spec:
        streams[f"{prefix}__substg1.0_10090102"] = spec["rtf"]
    if "date" in spec:
        fixed[(0x0E06, 0x0040)] = _filetime(spec["date"])
    streams[f"{prefix}__properties_version1.0"] = _properties(header, fixed)
    for number, (name, address, kind) in enumerate(spec.get("recipients", ())):
        storage = f"{prefix}__recip_version1.0_#{number:08X}/"
        text("3001", name, storage)
        text("39FE", address, storage)
        streams[f"{storage}__properties_version1.0"] = _properties(
            8, {(0x0C15, 0x0003): struct.pack("<I", kind)}
        )
    for number, attachment in enumerate(spec.get("attachments", ())):
        storage = f"{prefix}__attach_version1.0_#{number:08X}/"
        text("3707", attachment["name"], storage)
        if "message" in attachment:
            streams.update(
                message_streams(attachment["message"], f"{storage}__substg1.0_3701000D/", 24)
            )
            method = 5
        else:
            text("370E", attachment.get("mime", "application/octet-stream"), storage)
            streams[f"{storage}__substg1.0_37010102"] = attachment["data"]
            method = 1
        streams[f"{storage}__properties_version1.0"] = _properties(
            8, {(0x3705, 0x0003): struct.pack("<I", method)}
        )
    return streams


def build_msg(**spec: Any) -> bytes:
    return compound_file(message_streams(spec))
