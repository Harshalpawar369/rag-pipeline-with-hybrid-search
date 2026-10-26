from app.retrieval.search import keyword_query, rrf_fuse


def test_rrf_rewards_items_ranked_high_in_both_lists():
    fused = rrf_fuse([[1, 2, 3], [2, 4, 1]])
    order = [item for item, _ in fused]
    assert order[0] in (1, 2)
    assert set(order) == {1, 2, 3, 4}
    assert dict(fused)[2] > dict(fused)[3]


def test_rrf_uses_ranks_not_scores():
    # Rank 1 in one list contributes 1/61 regardless of what the underlying score was.
    fused = dict(rrf_fuse([[7], [8]]))
    assert abs(fused[7] - 1 / 61) < 1e-12
    assert abs(fused[8] - 1 / 61) < 1e-12


def test_keyword_query_is_or_of_sanitized_terms():
    q = keyword_query("What's the term & does it end? (early)")
    assert q.split(" | ")[:3] == ["What", "the", "term"] or "term" in q.split(" | ")
    assert "&" not in q and "(" not in q and "'" not in q
    assert " & " not in q
