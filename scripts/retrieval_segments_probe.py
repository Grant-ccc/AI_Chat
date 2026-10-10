"""Offline 2x2 experiment: whole/segmented query, identical final candidate budgets 3/8."""
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.retrieval import DEFAULT_MODEL, HybridRetriever, KeywordRetriever, SegmentedRetriever, SemanticRetriever, load_documents
from retrieval_probe import build_cases, build_gap_cases, summarize


def main():
    path = ROOT / 'knowledge/public.json'
    data, documents = load_documents(path)
    semantic = SemanticRetriever(documents, ROOT / '.local/embedding-models', local_only=True)
    hybrid = HybridRetriever(KeywordRetriever(documents), semantic)
    segmented = SegmentedRetriever(hybrid)
    cases = build_cases()
    # New development probes, not held-out tests. Expected IDs are evaluation-only.
    mixed = [dict(id='X01', query='二团单面款多少钱？我收到的伞坏了，想退款。', expected=['K11', 'K20']),
             dict(id='X02', query='有 UPF50+ 的报告吗？我想申请换货。', expected=['K06', 'K20']),
             dict(id='X03', query='手动和自动区别是什么？双面多少钱？', expected=['K10', 'K11'])]
    targets = [dict(id='A01', query='伞怎么收回去？', expected=['K12']),
               dict(id='A02', query='我买的是自动款\n不会收，有教程吗？', expected=['K12'])]
    groups = dict(development=cases, mixed=mixed, targets=targets, gaps=build_gap_cases())
    report = dict(mode='offline_segment_experiment', knowledge_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  source_sha256=data['source_sha256'], index_text='facts', model=DEFAULT_MODEL,
                  embedding_versions={name: version(name) for name in ['fastembed', 'onnxruntime', 'numpy']},
                  simulation_date=data['default_simulation_date'], hybrid_parameters=dict(rank_window=10, rank_constant=60),
                  segmented_parameters=dict(candidate_window=10, max_segments=4), results={}, summaries={})
    for budget in [3, 8]:
        for name, retriever in [('whole', hybrid), ('segmented', segmented)]:
            label = f'{name}-{budget}'
            report['results'][label] = {group: [dict(**case, **retriever.search(case['query'], budget)) for case in items]
                                        for group, items in groups.items()}
            report['summaries'][label] = {group: summarize(report['results'][label][group], budget)
                                          for group in ['development', 'mixed']}
            print(label + ': ' + json.dumps(report['summaries'][label], ensure_ascii=False), flush=True)
    directory = ROOT / '.local/retrieval'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = directory / (stamp + '-segments.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 分句检索与候选数量：离线对照', '',
             '相同知识、事实索引、向量模型与RRF参数；比较相同最终候选数量。开发对照不是回答正确率。', '',
             '| 方案 | 原18题找全预期依据 | 3道新增混合题找全预期依据 | 无关题有候选 |', '|---|---:|---:|---:|']
    for label, summary in report['summaries'].items():
        dev, mix = summary['development'], summary['mixed']
        lines.append(f"| {label} | {dev['all_expected_found']}/18 | {mix['all_expected_found']}/3 | {dev['negatives_with_candidates']}/5 |")
    for group, ids in [('development', ['T10', 'T11']), ('targets', ['A01', 'A02']), ('mixed', ['X01', 'X02', 'X03'])]:
        for id_ in ids:
            lines.extend(['', '## ' + id_])
            for label, results in report['results'].items():
                row = next(row for row in results[group] if row['id'] == id_)
                lines.extend(['', label + '：' + '、'.join(hit['id'] for hit in row['hits']),
                              '缺少：' + ('、'.join(sorted(set(row['expected']) - {h['id'] for h in row['hits']})) or '无'),
                              '实际查询：' + json.dumps(row.get('queries', [row['query']]), ensure_ascii=False)])
    regressions = []
    for budget in [3, 8]:
        before = report['results'][f'whole-{budget}']['development']
        after = report['results'][f'segmented-{budget}']['development']
        for base, candidate in zip(before, after):
            original_found = set(base['expected']) <= {hit['id'] for hit in base['hits']}
            new_found = set(candidate['expected']) <= {hit['id'] for hit in candidate['hits']}
            if original_found and not new_found:
                regressions.append(f"预算{budget}条：{base['id']}从找全变为漏检；原输入：{base['query']}；拆分：{candidate['queries']}")
    def candidate_bytes(label):
        rows = report['results'][label]['development'][:18]
        return round(sum(sum(len(json.dumps({key: hit[key] for key in ['id', 'topic', 'round', 'facts', 'boundaries', 'sources']},
            ensure_ascii=False).encode('utf-8')) for hit in row['hits']) for row in rows) / len(rows), 1)
    lines.extend(['', '## 决策与代价', '',
                  '分句未提高同预算下的整体效果，默认不启用；扩大到8条作为候选召回实验，也不直接改生成输入。', '',
                  '原18题整句候选平均JSON字节数：前3条' + str(candidate_bytes('whole-3')) +
                  '，前8条' + str(candidate_bytes('whole-8')) + '；不是tokens或付费估算。', '', *regressions,
                  '', '原开发表的T10/T11带有“未说明型号”“历史已说”等案例描述；拆句把描述误当独立问题。A01是实际调用的收伞问法，A02是显式多轮文本对照，不是数据库上下文管理。',
                  '', '## 限制', '',
                  '分句仅识别句末标点，不理解意图、指代或跨句条件；没有句末标点的多问、反事实和历史上下文需要另行处理。', '',
                  '候选增加会同时增加无关资料与模型输入成本；即使找到预期编号，也不证明资料充分、动作正确或模型引用正确。', '',
                  '5道无关题和6道缺少参数/实时事实的诊断题继续保留。未改默认检索方案，未调用付费模型。', ''])
    output.with_suffix('.md').write_text('\n'.join(lines), encoding='utf-8')
    print('可阅读对照：' + str(output.with_suffix('.md')))


if __name__ == '__main__':
    main()
