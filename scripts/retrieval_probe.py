"""本地检索对照：输出依据，不生成回答、不创建人工事项。"""
import argparse
import hashlib
import json
import sys
from importlib.metadata import version
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.retrieval import DEFAULT_MODEL, HybridRetriever, KeywordRetriever, SemanticRetriever, load_documents


def build_cases():
    # 仅读取开发集；保留题与参考答案绝不进入检索索引或向量模型输入。
    text = (ROOT / '花窗伞MVP_测试题与评分表_v0.1.md').read_text(encoding='utf-8')
    development = text.split('## 开发集：T01—T20', 1)[1].split('## 保留测试集', 1)[0]
    expected = {
        'T01': ['K02'], 'T02': ['K03'], 'T03': ['K04', 'K05'], 'T04': ['K06'],
        'T05': ['K07', 'K08', 'K09'], 'T06': ['P02'], 'T07': ['K08', 'K09'],
        'T08': ['K11'], 'T09': ['K10'], 'T10': ['K12'], 'T11': ['K12'],
        'T12': ['K15', 'K16'], 'T13': ['K16'], 'T14': ['K16'],
        'T15': ['K18'], 'T16': ['K19'], 'T17': ['K19', 'K20'], 'T18': ['K20'],
    }
    cases = []
    for line in development.splitlines():
        if not line.startswith('| T'):
            continue
        columns = [cell.strip() for cell in line.strip('|').split('|')]
        if columns[0] in expected:
            cases.append(dict(id=columns[0], query=columns[1], expected=expected[columns[0]]))
    cases.extend(dict(id=f'N{i:02d}', query=query, expected=[]) for i, query in enumerate([
        '今天北京天气怎么样？', '怎么安装打印机驱动？', '推荐一道番茄炒蛋做法。',
        '这个电脑的显卡是什么型号？', '帮我算一下房贷利息。',
    ], 1))
    if len(cases) != 23:
        raise ValueError('开发题解析数量异常')
    return cases


def summarize(rows, top_k):
    positives = [row for row in rows if row['expected']]
    recall = []
    precision = []
    complete = 0
    for row in positives:
        retrieved = {hit['id'] for hit in row['hits']}
        wanted = set(row['expected'])
        found = len(retrieved & wanted)
        recall.append(found / len(wanted))
        precision.append(found / len(retrieved) if retrieved else 0)
        complete += wanted <= retrieved
    negatives = [row for row in rows if not row['expected']]
    return dict(top_k=top_k, positive_cases=len(positives),
                mean_expected_recall=round(sum(recall)/len(recall), 4),
                mean_expected_precision=round(sum(precision)/len(precision), 4),
                all_expected_found=complete, negative_cases=len(negatives),
                negatives_with_candidates=sum(bool(row['hits']) for row in negatives),
                mean_query_ms=round(sum(row['elapsed_ms'] for row in rows)/len(rows), 3),
                note='预期条目是检索核对目标，不代表唯一可用依据；无关题返回候选不等于可回答。')


def analyze_semantic_scores(rows):
    """仅离线展示相关题/无关题分布与阈值代价，不输出上线建议阈值。"""
    if any(row['score_type'] != 'cosine' for row in rows):
        raise ValueError('分布分析只接受余弦检索结果')
    positives = [row for row in rows if row['expected']]
    negatives = [row for row in rows if not row['expected']]
    def top_score(row):
        return row['hits'][0]['score'] if row['hits'] else 0
    def extent(group):
        values = [top_score(row) for row in group]
        return dict(count=len(values), min=min(values) if values else None,
                    max=max(values) if values else None)
    sweep = []
    for threshold in [0.4, 0.45, 0.5, 0.6, 0.7, 0.8]:
        sweep.append(dict(threshold=threshold,
                          relevant_questions_blocked=[row['id'] for row in positives if top_score(row) < threshold],
                          unrelated_questions_kept=[row['id'] for row in negatives if top_score(row) >= threshold]))
    return dict(relevant_top_score=extent(positives), unrelated_top_score=extent(negatives),
                threshold_sweep=sweep,
                status='diagnostic_only',
                note='只在开发题上查看代价，没有独立阈值验证；相关题通过阈值不表示正确依据被找到或足够回答。')


def build_gap_cases():
    # 这些问题与商品有关，但现有知识不能提供所问的具体值/个案实时事实。
    # 候选资料可用于说明缺口；不能将它们视为零相关资料的纯无关题。
    return [dict(id=f'G{i:02d}', query=query, expected=[],
                 limitation='现有资料不能给出所问的具体值或实时个案信息；不得补造。')
            for i, query in enumerate([
                '伞面展开直径是多少厘米？', '这把伞净重多少克？',
                '自动款收起来长度是多少厘米？', '伞柄是什么材料？',
                '我的订单物流单号是多少？', '现在还剩多少把现货？',
            ], 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query')
    parser.add_argument('--context', action='append', default=[], help='已知用户上下文，可重复；不会读数据库')
    parser.add_argument('--evaluate', action='store_true')
    parser.add_argument('--method', choices=['keyword', 'semantic', 'hybrid', 'both', 'all'], default='both')
    parser.add_argument('--knowledge', type=Path, default=ROOT / 'knowledge/public.json')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--top-k', type=int, default=3)
    parser.add_argument('--local-only', action='store_true', help='语义模型仅从已有缓存加载，不下载')
    parser.add_argument('--rank-window', type=int, default=10, help='混合检索每路候选数，默认10')
    parser.add_argument('--rank-constant', type=int, default=60, help='RRF排序常数，默认60')
    parser.add_argument('--diagnose-gaps', action='store_true', help='额外查看6道相关但缺少具体答案的诊断题，不混入主评测指标')
    args = parser.parse_args()
    if bool(args.query) == args.evaluate:
        parser.error('请选择 --query 或 --evaluate 其中一种')
    if args.top_k < 1:
        parser.error('--top-k 必须大于零')
    if args.rank_window < 1 or args.rank_constant < 1:
        parser.error('--rank-window 和 --rank-constant 必须大于零')
    if args.method in ['hybrid', 'all'] and args.top_k > args.rank_window:
        parser.error('--top-k 不能超过 --rank-window')
    if args.query is not None and not args.query.strip():
        parser.error('--query 不能为空')
    if args.evaluate and args.context:
        parser.error('开发集使用各题自带上下文，不接受全局 --context')
    if args.diagnose_gaps and not args.evaluate:
        parser.error('--diagnose-gaps 需与 --evaluate 一起使用')
    data, documents = load_documents(args.knowledge)
    cases = build_cases() if args.evaluate else [dict(id='query', query='\n'.join(args.context + [args.query]), expected=[])]
    report = dict(created_at=datetime.now(timezone.utc).isoformat(),
                  knowledge_sha256=hashlib.sha256(args.knowledge.read_bytes()).hexdigest(),
                  source_sha256=data['source_sha256'], document_count=len(documents),
                  simulation_date=data['default_simulation_date'],
                  model=args.model if args.method != 'keyword' else None,
                  embedding_versions={name: version(name) for name in ['fastembed', 'onnxruntime', 'numpy']}
                  if args.method != 'keyword' else {},
                  hybrid_parameters=dict(rank_window=args.rank_window, rank_constant=args.rank_constant)
                  if args.method in ['hybrid', 'all'] else None,
                  results={}, summaries={})
    methods = {'both': ['keyword', 'semantic'], 'all': ['keyword', 'semantic', 'hybrid']}.get(args.method, [args.method])
    instances = {}
    def instance(method):
        if method not in instances:
            if method == 'keyword':
                instances[method] = KeywordRetriever(documents)
            elif method == 'semantic':
                instances[method] = SemanticRetriever(documents, ROOT / '.local/embedding-models', args.model, args.local_only)
            else:
                instances[method] = HybridRetriever(instance('keyword'), instance('semantic'), args.rank_window, args.rank_constant)
        return instances[method]
    for method in methods:
        print(f'初始化 {method} ...', flush=True)
        started = perf_counter()
        retriever = instance(method)
        setup_ms = round((perf_counter()-started)*1000, 3)
        rows = [dict(**case, **retriever.search(case['query'], args.top_k)) for case in cases]
        report['results'][method] = rows
        if args.evaluate:
            report['summaries'][method] = dict(setup_ms=setup_ms, **summarize(rows, args.top_k))
            print(json.dumps({method: report['summaries'][method]}, ensure_ascii=False), flush=True)
        else:
            print(json.dumps({method: rows[0]}, ensure_ascii=False, indent=2))
    if args.evaluate and 'semantic' in report['results']:
        report['semantic_score_diagnostic'] = analyze_semantic_scores(report['results']['semantic'])
        print(json.dumps({'semantic_score_diagnostic': report['semantic_score_diagnostic']}, ensure_ascii=False))
    if args.diagnose_gaps:
        report['gap_diagnostic'] = {method: [dict(**case, **instance(method).search(case['query'], args.top_k))
                                  for case in build_gap_cases()] for method in methods}
        print(json.dumps({'gap_diagnostic': {method: [dict(id=row['id'],
              top_id=row['hits'][0]['id'] if row['hits'] else None,
              top_score=row['hits'][0]['score'] if row['hits'] else None)
              for row in rows] for method, rows in report['gap_diagnostic'].items()}}, ensure_ascii=False))
    out = ROOT / '.local/retrieval'
    out.mkdir(parents=True, exist_ok=True)
    path = out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'结果已保存：{path}')


if __name__ == '__main__':
    main()
