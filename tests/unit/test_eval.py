import asyncio

from app.retrieval.search import Passage
from eval.run_eval import evaluate, rank_of_first_hit, score


def _p(title, content):
    return Passage(chunk_id=1, document_id="d", title=title, matter_id="m", content=content,
                   section_path="", page_start=None, page_end=None, view_label="accepted")


def test_rank_uses_title_and_normalized_snippet():
    gold = {"document_title": "msa.docx", "contains": "three   (3) years"}
    passages = [_p("nda.docx", "three (3) years"), _p("msa.docx", "The term is Three (3) Years.")]
    assert rank_of_first_hit(passages, gold) == 2


def test_scores_match_hand_computation():
    m = score([1, 3, None, 12])
    assert m["recall@5"] == 0.5
    assert m["recall@10"] == 0.5
    assert abs(m["mrr"] - (1 + 1 / 3 + 1 / 12) / 4) < 1e-9


def test_evaluate_runs_each_mode():
    items = [{"question": "q", "matter_ids": ["m"],
              "gold": {"document_title": "msa.docx", "contains": "three (3) years"}}]

    async def fake(mode, item):
        if mode == "hybrid":
            return [_p("msa.docx", "the term is three (3) years")]
        return [_p("other.docx", "nothing relevant")]

    results = asyncio.run(evaluate(items, fake))
    assert results["hybrid"]["recall@5"] == 1.0
    assert results["vector"]["recall@5"] == 0.0
