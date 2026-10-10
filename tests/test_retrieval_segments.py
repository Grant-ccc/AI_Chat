"""Generic segmentation and coverage tests, independent of business/model weights."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.retrieval import SegmentedRetriever, split_query_segments


def test_punctuation_split_preserves_clauses_without_rewriting():
    assert split_query_segments('多少钱？ 收到坏了，想退款。') == ['多少钱', '收到坏了，想退款']
    assert split_query_segments('柠檬茶多少钱？含糖吗？') == ['柠檬茶多少钱', '含糖吗']
    assert split_query_segments('没有句末标点，两个条件') == ['没有句末标点，两个条件']


def test_segment_limit_falls_back_without_dropping_later_requests():
    query = '一？二？三？四？五？'
    assert split_query_segments(query) == [query]
    with pytest.raises(ValueError):
        split_query_segments(' ')


class FakeRetriever:
    def __init__(self, routes):
        self.routes = routes
        self.queries = []

    def search(self, query, top_k):
        self.queries.append(query)
        return dict(score_type='rrf', hits=[dict(id=id_, score=1/(rank+1), facts=id_)
                    for rank, id_ in enumerate(self.routes[query][:top_k])])


def test_round_robin_deduplicates_preserves_provenance_and_does_not_add_scores():
    base = FakeRetriever({'价格': ['A', 'B', 'C'], '售后': ['A', 'D', 'E']})
    result = SegmentedRetriever(base).search('价格？售后？', 4)
    assert [hit['id'] for hit in result['hits']] == ['A', 'D', 'B', 'E']
    assert len(result['hits'][0]['segment_matches']) == 2
    assert result['hits'][0]['score'] == 1
    assert result['sufficiency'] == 'unvalidated'


def test_single_segment_keeps_original_query_and_ranking():
    base = FakeRetriever({'饮品含糖吗？': ['A', 'B', 'C']})
    result = SegmentedRetriever(base).search('饮品含糖吗？', 2)
    assert base.queries == ['饮品含糖吗？']
    assert [hit['id'] for hit in result['hits']] == ['A', 'B']


def test_empty_route_and_fewer_candidates_do_not_invent_documents():
    base = FakeRetriever({'甲': [], '乙': ['D']})
    assert [h['id'] for h in SegmentedRetriever(base).search('甲？乙？', 3)['hits']] == ['D']
    with pytest.raises(ValueError):
        SegmentedRetriever(base).search('甲？乙？', 11)
