import asyncio
import math

from app.retrieval.embeddings import HashEmbedder


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_output_is_deterministic_normalized_and_sized():
    emb = HashEmbedder(64)
    v1, v2 = asyncio.run(emb.embed(["termination notice", "termination notice"]))
    assert v1 == v2
    assert len(v1) == 64
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


def test_shared_vocabulary_scores_higher_than_unrelated_text():
    emb = HashEmbedder(512)
    query, related, unrelated = asyncio.run(emb.embed([
        "notice of termination of this agreement",
        "termination notice under this agreement",
        "sourdough bread recipe with rye flour",
    ]))
    assert _cos(query, related) > _cos(query, unrelated)


def test_empty_text_yields_a_zero_vector_without_dividing_by_zero():
    (vec,) = asyncio.run(HashEmbedder(8).embed([""]))
    assert vec == [0.0] * 8
