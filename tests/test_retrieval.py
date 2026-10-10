"""检索独立测试，不连接MySQL，不下载模型，不调用付费接口。"""
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
from app.retrieval import HybridRetriever, KeywordRetriever, fuse_rankings, load_documents
from export_public_knowledge import export
from retrieval_probe import analyze_semantic_scores, build_cases, build_gap_cases, summarize
from retrieval_review import compare_reports, render_review


def test_public_export_matches_source_and_excludes_internal_rules():
    data, docs = load_documents(ROOT / 'knowledge/public.json')
    assert data == export()
    assert {doc['id'] for doc in docs} == ({f'K{i:02d}' for i in range(1, 21)} |
                                             {f'P{i:02d}' for i in range(1, 9)})
    serialized = json.dumps(data, ensure_ascii=False)
    assert '内部另留一周' not in serialized
    assert '参考回答' not in serialized
    assert all(doc['sources'] and doc['boundaries'] for doc in docs)


def test_non_public_document_is_rejected(tmp_path):
    data = export()
    data['products'][0]['visibility'] = 'merchant'
    path = tmp_path / 'private.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='公开知识'):
        load_documents(path)


def test_unrelated_business_works_without_algorithm_changes(tmp_path):
    data = dict(schema_version=1, sources={'manual': {'name': '饮品说明'}}, products=[],
                entries=[dict(id='DRINK01', topic='饮品配料', round='夏季', visibility='public',
                              facts='柠檬茶含有柠檬片。', boundaries='不承诺适合所有过敏人群。',
                              source_ids=['manual'])])
    path = tmp_path / 'drinks.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    _, docs = load_documents(path)
    hit = KeywordRetriever(docs).search('柠檬茶的配料')['hits'][0]
    assert hit['id'] == 'DRINK01'
    assert hit['sources']['manual']['name'] == '饮品说明'
    assert hit['boundaries'] == data['entries'][0]['boundaries']


def test_unknown_question_is_not_forced_into_keyword_results():
    _, docs = load_documents(ROOT / 'knowledge/public.json')
    result = KeywordRetriever(docs).search('打印机驱动怎么安装？')
    assert result['hits'] == []
    assert result['sufficiency'] == 'unvalidated'


def test_known_missing_report_is_a_retrievable_fact():
    _, docs = load_documents(ROOT / 'knowledge/public.json')
    result = KeywordRetriever(docs).search('防晒检测报告')
    assert 'K06' in [hit['id'] for hit in result['hits']]
    assert all('search_text' not in hit for hit in result['hits'])


def test_probe_uses_only_development_and_explicit_negative_cases():
    cases = build_cases()
    assert [case['id'] for case in cases[:18]] == [f'T{i:02d}' for i in range(1, 19)]
    assert len([case for case in cases if not case['expected']]) == 5
    assert '自动款' in next(case['query'] for case in cases if case['id'] == 'T11')


def test_metrics_count_all_required_evidence_and_negative_candidates():
    rows = [dict(expected=['K04', 'K05'], hits=[dict(id='K04')], elapsed_ms=1),
            dict(expected=[], hits=[dict(id='K01')], elapsed_ms=3)]
    summary = summarize(rows, 3)
    assert summary['mean_expected_recall'] == 0.5
    assert summary['all_expected_found'] == 0
    assert summary['negatives_with_candidates'] == 1
    assert summary['mean_query_ms'] == 2


def test_fusion_deduplicates_and_uses_rank_not_incompatible_raw_scores():
    keyword = dict(score_type='bm25', hits=[dict(id='A', score=999), dict(id='B', score=1)])
    semantic = dict(score_type='cosine', hits=[dict(id='B', score=0.99), dict(id='C', score=0.98)])
    result = fuse_rankings(keyword, semantic)
    assert [hit['id'] for hit in result['hits']] == ['B', 'A', 'C']
    assert result['hits'][0]['components']['keyword']['rank'] == 2
    assert result['sufficiency'] == 'unvalidated'
    keyword['hits'][0]['score'] = 0.01
    assert [hit['id'] for hit in fuse_rankings(keyword, semantic)['hits']] == ['B', 'A', 'C']


def test_fusion_handles_one_empty_route_without_inventing_candidates():
    empty = dict(score_type='bm25', hits=[])
    semantic = dict(score_type='cosine', hits=[dict(id='A', score=0.01)])
    assert fuse_rankings(empty, semantic)['hits'][0]['id'] == 'A'
    assert fuse_rankings(empty, empty)['hits'] == []
    with pytest.raises(ValueError, match='重复'):
        fuse_rankings(empty, dict(score_type='cosine', hits=semantic['hits'] * 2))


def test_hybrid_can_recover_evidence_from_both_routes():
    class Route:
        def __init__(self, hits, score_type):
            self.hits = hits
            self.score_type = score_type
        def search(self, query, top_k):
            assert query == '两个问题'
            return dict(hits=self.hits[:top_k], score_type=self.score_type)
    retriever = HybridRetriever(Route([dict(id='A', facts='依据一', score=10)], 'bm25'),
                                Route([dict(id='B', facts='依据二', score=0.7)], 'cosine'))
    result = retriever.search('两个问题', 2)
    assert {hit['facts'] for hit in result['hits']} == {'依据一', '依据二'}
    with pytest.raises(ValueError, match='rank_window'):
        retriever.search('两个问题', 11)


def test_threshold_diagnostic_counts_rejected_relevant_and_kept_unrelated():
    rows = [dict(id='T01', expected=['K02'], score_type='cosine', hits=[dict(id='K01', score=0.42)]),
            dict(id='N01', expected=[], score_type='cosine', hits=[dict(id='K02', score=0.43)])]
    result = analyze_semantic_scores(rows)
    threshold = next(row for row in result['threshold_sweep'] if row['threshold'] == 0.45)
    assert threshold['relevant_questions_blocked'] == ['T01']
    assert threshold['unrelated_questions_kept'] == []
    assert result['status'] == 'diagnostic_only'
    with pytest.raises(ValueError, match='余弦'):
        analyze_semantic_scores([dict(score_type='rrf')])


def test_gap_diagnostics_are_separate_from_main_recall_evaluation():
    main_ids = {case['id'] for case in build_cases()}
    gaps = build_gap_cases()
    assert len(gaps) == 6
    assert not main_ids & {case['id'] for case in gaps}
    assert all(case['limitation'] for case in gaps)


def test_boundary_index_preserves_facts_and_does_not_turn_constraints_into_facts():
    _, baseline = load_documents(ROOT / 'knowledge/public.json')
    _, candidate = load_documents(ROOT / 'knowledge/public.json', 'facts-boundaries')
    for before, after in zip(baseline, candidate):
        assert before['facts'] == after['facts']
        assert before['boundaries'] == after['boundaries']
        assert after['search_text'] == before['search_text'] + '。回答边界：' + before['boundaries']
    with pytest.raises(ValueError, match='索引文本'):
        load_documents(ROOT / 'knowledge/public.json', 'unknown')


def test_review_distinguishes_retrieval_changes_from_fact_support():
    hit = dict(id='K05', topic='伞骨', facts='伞骨为不锈钢', boundaries='不能推断手柄材质',
               round='二团', sources={'S04': {'name': '参与者确认'}}, score=0.71)
    old = dict(id='T01', query='伞柄材质？', expected=['K05'], hits=[], score_type='rrf')
    new = dict(**old, limitation='不能提供手柄材质')
    new['hits'] = [hit]
    baseline = dict(knowledge_sha256='same', results={'hybrid': [old]},
                    summaries={'hybrid': dict(top_k=3, all_expected_found=0, positive_cases=1)})
    candidate = dict(knowledge_sha256='same', results={'hybrid': [new]},
                     summaries={'hybrid': dict(top_k=3, all_expected_found=1, positive_cases=1)})
    item = compare_reports(baseline, candidate, 'hybrid')[0]
    assert item['gained'] == ['K05']
    assert item['review_status'] == 'pending'
    markdown = render_review(baseline, candidate)
    assert '事实：伞骨为不锈钢' in markdown
    assert '回答边界：不能推断手柄材质' in markdown
    assert '待人工复核' in markdown
    assert '来源：S04：参与者确认' in markdown


def test_review_rejects_changed_knowledge_questions_and_result_count():
    import copy
    row = dict(id='T01', query='下雨能用吗？', expected=['K02'], hits=[], score_type='rrf')
    baseline = dict(knowledge_sha256='same', results={'hybrid': [row]},
                    summaries={'hybrid': dict(top_k=3, all_expected_found=0, positive_cases=1)})
    candidate = copy.deepcopy(baseline)
    candidate['knowledge_sha256'] = 'different'
    with pytest.raises(ValueError, match='对照条件'):
        compare_reports(baseline, candidate, 'hybrid')
    candidate = copy.deepcopy(baseline)
    candidate['results']['hybrid'][0]['query'] = '修改后的问题'
    with pytest.raises(ValueError, match='问题或预期'):
        compare_reports(baseline, candidate, 'hybrid')
    candidate = copy.deepcopy(baseline)
    candidate['summaries']['hybrid']['top_k'] = 5
    with pytest.raises(ValueError, match='条数'):
        render_review(baseline, candidate)
