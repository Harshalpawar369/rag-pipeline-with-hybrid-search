import zipfile

import pytest

from app.ingestion.validate import RejectedDocument, check_docx_archive, sniff_type


def test_sniff_by_signature_not_extension():
    assert sniff_type(b"%PDF-1.7 rest") == "pdf"
    assert sniff_type(b"PK\x03\x04....") == "docx"
    assert sniff_type(b"MZ\x90\x00") is None


def test_zip_bomb_rejected(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"0" * (20 * 1024 * 1024))
    with pytest.raises(RejectedDocument) as exc:
        check_docx_archive(path)
    assert exc.value.code == "zip_limits"


def test_missing_document_part_rejected(tmp_path):
    path = tmp_path / "notword.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(RejectedDocument) as exc:
        check_docx_archive(path)
    assert exc.value.code == "not_docx"


def test_path_traversal_entry_rejected(tmp_path):
    path = tmp_path / "evil.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../../etc/passwd", "x")
        zf.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(RejectedDocument) as exc:
        check_docx_archive(path)
    assert exc.value.code == "unsafe_archive"
