"""Local cross-encoder experiment; relevance ranking never implies factual support."""
import math
from pathlib import Path
from time import perf_counter

DEFAULT_RERANKER = 'BAAI/bge-reranker-base'


class CrossEncoderReranker:
    def __init__(self, cache_dir, model=DEFAULT_RERANKER, local_only=True):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self.model_name = model
        self.encoder = TextCrossEncoder(model, cache_dir=str(cache_dir), threads=2, local_files_only=local_only)
        # FastEmbed 0.7.4 exposes the downloaded snapshot directory on its ONNX model.
        self.model_revision = Path(self.encoder.model._model_dir).name

    def score(self, query, texts):
        return list(self.encoder.rerank(query, texts, batch_size=8))


def rerank_candidates(query, result, scorer, top_k=3):
    """Rerank the exact frozen pool; preserve documents and original rank/score provenance."""
    if not query.strip() or top_k < 1:
        raise ValueError('问题不能为空，最终候选数须为正数')
    started = perf_counter()
    hits = result['hits']
    if any(hit.get('visibility') != 'public' for hit in hits):
        raise ValueError('只允许公开候选进入重排')
    if len({hit['id'] for hit in hits}) != len(hits):
        raise ValueError('原候选编号重复')
    texts = [f"{hit['topic']}。{hit['facts']}" for hit in hits]
    scores = list(scorer.score(query, texts)) if texts else []
    if len(scores) != len(hits) or any(not math.isfinite(float(score)) for score in scores):
        raise ValueError('重排分数数量异常或非有限值')
    ranked = [dict(hit, score=float(score), retrieval_rank=rank+1, retrieval_score=hit['score'],
                   retrieval_score_type=result['score_type']) for rank, (hit, score) in enumerate(zip(hits, scores))]
    ranked.sort(key=lambda hit: (-hit['score'], hit['id']))
    return dict(hits=ranked[:top_k], score_type='cross_encoder_raw', candidate_count=len(hits),
                candidate_ids=[hit['id'] for hit in hits], full_reranking=ranked,
                elapsed_ms=round((perf_counter()-started)*1000, 3), sufficiency='unvalidated',
                warning='分数仅描述问题与资料的相关程度，不是答案可信度或资料充分性。')


class RerankedRetriever:
    def __init__(self, retriever, scorer, candidate_pool=8):
        if candidate_pool < 1:
            raise ValueError('召回池须为正数')
        self.retriever, self.scorer, self.candidate_pool = retriever, scorer, candidate_pool

    def search(self, query, top_k=3):
        if not 1 <= top_k <= self.candidate_pool:
            raise ValueError('最终候选数不能超过召回池')
        started = perf_counter()
        candidates = self.retriever.search(query, self.candidate_pool)
        result = rerank_candidates(query, candidates, self.scorer, top_k)
        result['retrieval_elapsed_ms'] = candidates.get('elapsed_ms')
        result['total_elapsed_ms'] = round((perf_counter()-started)*1000, 3)
        return result
