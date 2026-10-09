"""检索独立测试，不连接MySQL，不下载模型，不调用付费接口。"""
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
from app.retrieval import KeywordRetriever, load_documents
from export_public_knowledge import export
from retrieval_probe import build_cases, summarize


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
