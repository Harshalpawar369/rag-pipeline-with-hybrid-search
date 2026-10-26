from app.ingestion.chunk import _PathState, _classify, chunk_blocks
from app.ingestion.parse import Block


def test_article_heading_merges_into_following_clause():
    blocks = [
        Block("ARTICLE 1 DEFINITIONS", 1, False),
        Block('1.1 "Confidential Information" means all non-public information disclosed by a party.', 1),
    ]
    chunks = chunk_blocks(blocks, "accepted")
    assert len(chunks) == 1
    assert chunks[0].section_path == "1.1"
    assert chunks[0].content.startswith("ARTICLE 1 DEFINITIONS")


def test_numbered_clauses_split_with_paths_and_pages():
    blocks = [
        Block("1.1 Definitions apply throughout this agreement and bind both parties.", 1),
        Block("1.2 The term runs for three years from the effective date of signature.", 2),
    ]
    chunks = chunk_blocks(blocks, "accepted")
    assert [c.section_path for c in chunks] == ["1.1", "1.2"]
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)


def test_sub_items_get_their_own_path_when_chunks_split():
    blocks = [
        Block("3.1 Fees are payable within thirty days of the invoice date. " + "x" * 40, None),
        Block("(a) late payments accrue interest at the statutory rate. " + "y" * 150, None),
        Block("(b) disputed amounts must be raised in writing. " + "z" * 150, None),
    ]
    chunks = chunk_blocks(blocks, "accepted", max_chars=150)
    paths = [c.section_path for c in chunks]
    assert "3.1" in paths
    assert "3.1 > (a)" in paths
    assert "3.1 > (b)" in paths


def test_roman_under_letter_classifies_as_sub_sub():
    st = _PathState()
    _classify(Block("3.1 Scope", None), st)
    _classify(Block("(a) first limb", None), st)
    kind = _classify(Block("(i) a narrow sub-limb", None), st)
    assert kind == "sub"
    assert st.label() == "3.1 > (a) > (i)"


def test_next_letter_is_not_roman():
    st = _PathState()
    _classify(Block("3.1 Scope", None), st)
    _classify(Block("(h) an item", None), st)
    _classify(Block("(i) the next letter", None), st)
    assert st.label() == "3.1 > (i)"


def test_nul_bytes_are_stripped():
    chunks = chunk_blocks([Block("1.1 Clause with a\x00 null byte inside it, so the paragraph carries a full sentence of text.", 1)], "accepted")
    assert "\x00" not in chunks[0].content
