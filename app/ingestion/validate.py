"""Upload validation. File type comes from content signatures, not the extension.
DOCX archives are checked for zip bombs and unsafe entry names before parsing."""
from __future__ import annotations

import zipfile
from pathlib import Path

PDF_SIG = b"%PDF-"
ZIP_SIG = b"PK\x03\x04"
DOCX_MAIN_PART = "word/document.xml"

MAX_ZIP_ENTRIES = 500
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100
MAX_ZIP_DEPTH = 10


class RejectedDocument(Exception):
    """A file that will never be indexed. Not retried."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sniff_type(head: bytes) -> str | None:
    """Return 'pdf', 'docx' (a zip, confirmed later), or None."""
    if head.startswith(PDF_SIG):
        return "pdf"
    if head.startswith(ZIP_SIG):
        return "docx"
    return None


def check_docx_archive(path: Path) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise RejectedDocument("bad_archive", "The file is not a valid DOCX archive.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise RejectedDocument("zip_limits", "The DOCX archive has too many entries.")
        total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise RejectedDocument("encrypted", "Encrypted DOCX files are not supported.")
            name = info.filename
            if name.startswith("/") or ".." in name.split("/") or name.count("/") > MAX_ZIP_DEPTH:
                raise RejectedDocument("unsafe_archive", "The DOCX archive has unsafe entry names.")
            total += info.file_size
            if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_ZIP_RATIO):
                raise RejectedDocument("zip_limits", "The DOCX archive fails the compression-ratio check.")
            if total > MAX_ZIP_TOTAL_BYTES:
                raise RejectedDocument("zip_limits", "The DOCX archive expands beyond the allowed size.")
        if DOCX_MAIN_PART not in {i.filename for i in infos}:
            raise RejectedDocument("not_docx", "The archive is not a Word document.")


def scan_for_malware(path: Path) -> None:
    """Hook for a malware scanner (for example ClamAV, or Microsoft Defender for Endpoint in Azure).
    Not implemented in this version: no scan runs."""
    return None
