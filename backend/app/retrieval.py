"""独立检索模块：数据驱动，不访问数据库或生成模型，不执行交接动作。"""
import json
import math
import re
from collections import Counter
from pathlib import Path
from time import perf_counter

DEFAULT_MODEL = 'BAAI/bge-small-zh-v1.5'


def load_documents(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('不支持的知识文件版本')
    docs = []
    for entry in data['entries'] + data['products']:
        if entry.get('visibility') != 'public':
            raise ValueError('用户检索数据只能包含公开知识')
        refs = {ref: data['sources'][ref] for ref in entry['source_ids']}
        docs.append({**entry, 'sources': refs,
                     'search_text': f"{entry['topic']}。{entry['facts']}"})
    ids = [doc['id'] for doc in docs]
    if not docs or len(ids) != len(set(ids)):
        raise ValueError('知识条目为空或编号重复')
    return data, docs


def terms(text):
    """通用中文字符二元组与英文/数字词，无商品专用词典。"""
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    for span in re.findall(r'[\u4e00-\u9fff]+', text):
        tokens.extend(span[i:i+2] for i in range(len(span)-1))
        if len(span) == 1:
            tokens.append(span)
    return tokens


class KeywordRetriever:
    def __init__(self, documents):
        self.documents = documents
        self.counts = [Counter(terms(doc['search_text'])) for doc in documents]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average_length = sum(self.lengths) / len(self.lengths) or 1
        self.df = Counter(token for count in self.counts for token in count)

    def search(self, query, top_k=3):
        if not query.strip():
            raise ValueError('检索问题不能为空')
        started = perf_counter()
        scores = []
        for count, length in zip(self.counts, self.lengths):
            value = 0.0
            for token in set(terms(query)):
                freq = count[token]
                if not freq:
                    continue
                df = self.df[token]
                idf = math.log(1 + (len(self.counts)-df+0.5)/(df+0.5))
                value += idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * length / self.average_length))
            scores.append(value)
        return ranked(self.documents, scores, top_k, 'bm25', started, positive_only=True)


class SemanticRetriever:
    def __init__(self, documents, cache_dir, model=DEFAULT_MODEL, local_only=False):
        import numpy as np
        from fastembed import TextEmbedding
        self.np = np
        self.documents = documents
        self.model_name = model
        self.encoder = TextEmbedding(model_name=model, cache_dir=str(cache_dir), threads=2,
                                     local_files_only=local_only)
        # 使用模型提供的 passage/query 接口，不统一套用其他模型的前缀。
        self.vectors = np.array(list(self.encoder.passage_embed(
            [doc['search_text'] for doc in documents])))
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        self.vectors = self.vectors / np.maximum(norms, 1e-12)

    def search(self, query, top_k=3):
        if not query.strip():
            raise ValueError('检索问题不能为空')
        started = perf_counter()
        vector = next(self.encoder.query_embed(query))
        vector = vector / max(float(self.np.linalg.norm(vector)), 1e-12)
        scores = self.vectors @ vector
        return ranked(self.documents, scores, top_k, 'cosine', started)


def fuse_rankings(keyword, semantic, top_k=3, rank_constant=60):
    """按名次融合；不把 BM25 与余弦分数相加，也不判断资料充分性。"""
    if top_k < 1 or rank_constant < 1:
        raise ValueError('top_k 和 rank_constant 必须大于零')
    candidates = {}
    for method, result in [('keyword', keyword), ('semantic', semantic)]:
        seen = set()
        for rank, hit in enumerate(result['hits'], 1):
            if hit['id'] in seen:
                raise ValueError('单路检索结果存在重复编号')
            seen.add(hit['id'])
            item = candidates.setdefault(hit['id'], dict(
                document={k: v for k, v in hit.items() if k != 'score'},
                score=0.0, components={}))
            item['score'] += 1 / (rank_constant + rank)
            item['components'][method] = dict(rank=rank, score=hit['score'],
                                               score_type=result['score_type'])
    ordered = sorted(candidates.items(), key=lambda pair: (-pair[1]['score'], pair[0]))
    return dict(hits=[item['document'] | dict(score=round(item['score'], 6),
                 components=item['components']) for _, item in ordered[:top_k]],
                score_type='rrf', rank_constant=rank_constant,
                sufficiency='unvalidated',
                warning='融合分数仅用于排序，不能作为回答可信度或资料充分性的阈值。')


class HybridRetriever:
    def __init__(self, keyword, semantic, rank_window=10, rank_constant=60):
        if rank_window < 1 or rank_constant < 1:
            raise ValueError('rank_window 和 rank_constant 必须大于零')
        self.keyword = keyword
        self.semantic = semantic
        self.rank_window = rank_window
        self.rank_constant = rank_constant

    def search(self, query, top_k=3):
        if top_k < 1 or top_k > self.rank_window:
            raise ValueError('top_k 必须在1和rank_window之间')
        started = perf_counter()
        keyword = self.keyword.search(query, self.rank_window)
        semantic = self.semantic.search(query, self.rank_window)
        result = fuse_rankings(keyword, semantic, top_k, self.rank_constant)
        result['rank_window'] = self.rank_window
        result['elapsed_ms'] = round((perf_counter()-started)*1000, 3)
        return result


def ranked(documents, scores, top_k, score_type, started, positive_only=False):
    if top_k < 1:
        raise ValueError('top_k 必须大于零')
    ordered = sorted(zip(documents, scores), key=lambda pair: (-float(pair[1]), pair[0]['id']))
    hits = [{k: v for k, v in doc.items() if k != 'search_text'} | {'score': round(float(score), 6)}
            for doc, score in ordered if not positive_only or score > 0][:top_k]
    return dict(hits=hits, score_type=score_type,
                elapsed_ms=round((perf_counter()-started)*1000, 3),
                sufficiency='unvalidated',
                warning='匹配分数不是答案可信度；当前未校准资料充分性阈值。')
