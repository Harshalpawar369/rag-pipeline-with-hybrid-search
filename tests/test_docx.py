from app.ingestion.chunk import chunk_blocks
from app.ingestion.parse import parse_docx
from scripts.samples import build_sample_docx


def test_tracked_changes_produce_accepted_and_original_views(tmp_path):
    path = build_sample_docx(tmp_path / "msa.docx")
    parsed = parse_docx(path)
    assert set(parsed.views) == {"accepted", "original"}

    accepted = " ".join(b.text for b in parsed.views["accepted"])
    original = " ".join(b.text for b in parsed.views["original"])
    assert "three (3) years" in accepted and "two (2) years" not in accepted
    assert "two (2) years" in original and "three (3) years" not in original
    assert "the fees paid in the prior twelve (12) months" in accepted
    assert "the fees paid in the prior six (6) months" in original


def test_views_are_chunked_with_labels(tmp_path):
    path = build_sample_docx(tmp_path / "msa.docx")
    parsed = parse_docx(path)
    accepted = chunk_blocks(parsed.views["accepted"], "accepted")
    original = chunk_blocks(parsed.views["original"], "original")
    assert all(c.view_label == "accepted" for c in accepted)
    assert all(c.view_label == "original" for c in original)
    term = next(c for c in accepted if c.section_path == "1.2")
    assert "three (3) years" in term.content


def test_document_without_tracked_changes_has_one_view(tmp_path):
    from docx import Document
    doc = Document()
    doc.add_paragraph("1.1 A plain clause with no revisions anywhere in this paragraph at all.")
    path = tmp_path / "plain.docx"
    doc.save(path)
    parsed = parse_docx(path)
    assert list(parsed.views) == ["accepted"]
