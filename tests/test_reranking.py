"""Candidate filtering safeguards; mock scores require no weights/downloads."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.reranking import RerankedRetriever, rerank_candidates


def candidates():
    return dict(score_type='rrf',hits=[dict(id=id_,score=100-index,visibility='public',topic='配料',
                facts=f'{id_}饮品含柠檬',boundaries='不确认库存',sources={'manual':'说明'})
                for index,id_ in enumerate(['A','B','C','D'])])


class Scorer:
    def __init__(self,scores):
        self.scores=scores
        self.seen=[]
    def score(self,query,texts):
        self.seen.append((query,texts))
        return self.scores


def test_reranking_keeps_exact_document_and_provenance_and_ignores_original_score():
    original=candidates()
    scorer=Scorer([-2,3,1,0])
    result=rerank_candidates('饮品配料',original,scorer,2)
    assert [h['id'] for h in result['hits']]==['B','C']
    assert result['hits'][0]['retrieval_rank']==2
    assert result['hits'][0]['retrieval_score']==99
    assert result['hits'][0]['facts']==original['hits'][1]['facts']
    assert original['hits'][1]['score']==99
    assert scorer.seen[0][1]==[h['topic']+'。'+h['facts'] for h in original['hits']]
    assert result['sufficiency']=='unvalidated'


@pytest.mark.parametrize('scores',[[1], [1,2,3,float('nan')],[1,2,3,float('inf')]])
def test_incomplete_or_invalid_scores_are_rejected(scores):
    with pytest.raises(ValueError):
        rerank_candidates('配料',candidates(),Scorer(scores))


def test_private_and_duplicate_candidates_rejected_before_scoring():
    private=candidates()
    private['hits'][0]['visibility']='merchant'
    duplicate=candidates()
    duplicate['hits'][1]['id']='A'
    scorer=Scorer([])
    for result in [private,duplicate]:
        with pytest.raises(ValueError):
            rerank_candidates('配料',result,scorer)
    assert scorer.seen==[]


def test_empty_pool_does_not_score_or_invent_documents():
    scorer=Scorer([])
    result=rerank_candidates('配料',dict(score_type='rrf',hits=[]),scorer)
    assert result['hits']==[]
    assert scorer.seen==[]


def test_wrapper_fetches_larger_pool_but_only_returns_requested_final_count():
    class Retriever:
        def search(self,query,top_k):
            assert top_k==4
            return candidates()
    retriever=RerankedRetriever(Retriever(),Scorer([0,1,2,3]),4)
    assert [h['id'] for h in retriever.search('配料',2)['hits']]==['D','C']
    with pytest.raises(ValueError):
        retriever.search('配料',5)
