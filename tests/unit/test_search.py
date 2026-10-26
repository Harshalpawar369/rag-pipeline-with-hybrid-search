from app.retrieval.search import keyword_query, rrf_fuse


def test_rrf_rewards_items_ranked_high_in_both_lists():
    fused = rrf_fuse([[1, 2, 3], [2, 4, 1]])
    order = [item for item, _ in fused]
    assert order[0] in (1, 2)
    assert set(order) == {1, 2, 3, 4}
    assert dict(fused)[2] > dict(fused)[3]


def test_rrf_uses_ranks_not_scores():
    fused = dict(rrf_fuse([[7], [8]]))
    assert abs(fused[7] - 1 / 61) < 1e-12
    assert abs(fused[8] - 1 / 61) < 1e-12


def test_keyword_query_is_an_or_of_alphanumeric_terms():
    assert keyword_query("What's the term & does it end? (early)") == "What | the | term | does | it | end | early"


def test_keyword_query_cannot_inject_tsquery_operators():
    q = keyword_query("cat' & dog | !bird:* <-> fish")
    assert all(ch not in q for ch in "'&!:*<>()")
    assert q.split(" | ") == ["cat", "dog", "bird", "fish"]
