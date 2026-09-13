"""
Format detection ("sniffing") for inbound source files.

File extensions are not trustworthy here -- confirmed directly against the
real sample data under `14072026/` (see the approved plan's "Key
findings"): CBS's `.xls` export is actually SpreadsheetML XML, and NTSL's
`.xls` export is actually an OOXML zip. This module detects the real
format from leading bytes / zip member content, independent of the
declared extension. Callers (file_processing/tasks.py) cross-check the
sniffed result against `FileTypeConfig.payload_format` -- a mismatch is a
rejection (FORMAT_MISMATCH), not a silent best-effort parse.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ContainerKind(str, Enum):
    ZIP = "ZIP"
    NONE = "NONE"


class PayloadKind(str, Enum):
    SPREADSHEETML_XML = "SPREADSHEETML_XML"
    OOXML_ZIP = "OOXML_ZIP"
    CSV = "CSV"
    UNKNOWN = "UNKNOWN"


@dataclass
class SniffResult:
    container: ContainerKind
    # None if the file is still wrapped in a container (zip) that must be
    # unwrapped (extracted) before the real payload format can be
    # determined.
    payload: PayloadKind | None
    detail: str = ""


_ZIP_SIGNATURE = b"PK\x03\x04"
_SPREADSHEETML_MARKERS = (b"<?xml", b"urn:schemas-microsoft-com:office:spreadsheet")


def _classify_zip(names: list[str]) -> SniffResult:
    if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names):
        return SniffResult(container=ContainerKind.ZIP, payload=PayloadKind.OOXML_ZIP)

    # Zip contents not recognized as any known payload -- payload format
    # isn't determined yet. Real zip-member extraction isn't built here
    # (inner_file_pattern has never been wired to any code); this just
    # keeps the container=ZIP/payload=None fallback so format_sniff_and_parse
    # still produces a clear UNSUPPORTED_FORMAT exception rather than
    # crashing if an unexpected zip shows up.
    return SniffResult(container=ContainerKind.ZIP, payload=None, detail=f"unrecognized zip members={names}")


def _classify_non_zip(head: bytes, peek: bytes) -> SniffResult:
    if any(marker in peek[:4000] for marker in _SPREADSHEETML_MARKERS) and b"<Workbook" in peek[:4000]:
        return SniffResult(container=ContainerKind.NONE, payload=PayloadKind.SPREADSHEETML_XML)

    # CSV has no reliable signature of its own -- deliberately checked last.
    # Anything not otherwise identified either really is delimited text, or
    # is a genuinely unrecognized format that downstream structural
    # validation should reject rather than this sniffer guessing further.
    return SniffResult(container=ContainerKind.NONE, payload=PayloadKind.CSV)


def sniff_bytes(data: bytes) -> SniffResult:
    """
    Classify an in-memory buffer. Intended for unit tests and for small
    already-decrypted/unzipped payloads; for real files on disk prefer
    `sniff_path`, which avoids reading multi-hundred-MB files fully just to
    sniff them.
    """
    head = data[:32]
    if head.startswith(_ZIP_SIGNATURE):
        try:
            from io import BytesIO

            names = zipfile.ZipFile(BytesIO(data)).namelist()
        except zipfile.BadZipFile as exc:
            return SniffResult(container=ContainerKind.ZIP, payload=PayloadKind.UNKNOWN, detail=str(exc))
        return _classify_zip(names)

    return _classify_non_zip(head, data)


def sniff_path(path: Path, peek_bytes: int = 8192) -> SniffResult:
    """
    Classify a file on disk. Reads only a small head for the common cases;
    only opens the file as a zip (via `zipfile.ZipFile(path)`, which seeks
    to the central directory rather than reading the whole file) when the
    leading bytes indicate a zip signature.
    """
    with open(path, "rb") as fh:
        head = fh.read(peek_bytes)

    if head.startswith(_ZIP_SIGNATURE):
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile as exc:
            return SniffResult(container=ContainerKind.ZIP, payload=PayloadKind.UNKNOWN, detail=str(exc))
        return _classify_zip(names)

    return _classify_non_zip(head, head)
