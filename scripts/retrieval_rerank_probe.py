"""Reorder saved whole-query eight-document pools locally; no paid model requests."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.reranking import CrossEncoderReranker, DEFAULT_RERANKER, rerank_candidates
from retrieval_probe import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='已保存的分句实验JSON，使用whole-8固定候选池')
    parser.add_argument('--allow-download', action='store_true', help='首次可下载公开模型；默认仅本地缓存')
    args = parser.parse_args()
    original = json.loads(args.input.read_text(encoding='utf-8'))
    if original['knowledge_sha256'] != hashlib.sha256((ROOT / 'knowledge/public.json').read_bytes()).hexdigest():
        raise ValueError('原报告与当前知识文件不一致')
    groups = original['results']['whole-8']
    if any(len(row['hits']) > 8 or row['score_type'] != 'rrf' for rows in groups.values() for row in rows):
        raise ValueError('只接受最多8条的原RRF候选池')
    started = perf_counter()
    scorer = CrossEncoderReranker(ROOT / '.local/reranker-models', local_only=not args.allow_download)
    report = dict(mode='offline_rerank_experiment', source_report=str(args.input.resolve()),
                  source_report_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                  knowledge_sha256=original['knowledge_sha256'], source_sha256=original['source_sha256'],
                  simulation_date=original['simulation_date'], index_text=original['index_text'],
                  retriever_model=original['model'], embedding_versions=original['embedding_versions'],
                  hybrid_parameters=original['hybrid_parameters'], reranker_model=DEFAULT_RERANKER,
                  reranker_revision=scorer.model_revision,
                  reranker_input='topic+facts', library_versions={name: version(name) for name in ['fastembed','onnxruntime','numpy']},
                  setup_ms=round((perf_counter()-started)*1000,3), results={}, summaries={})
    for group, rows in groups.items():
        report['results'][group] = []
        for row in rows:
            ranking = rerank_candidates(row['query'], row, scorer)
            report['results'][group].append(dict(id=row['id'], query=row['query'], expected=row['expected'],
                                                 baseline_ids=[hit['id'] for hit in row['hits'][:3]], **ranking))
        if group in ['development','mixed','targets']:
            report['summaries'][group] = summarize(report['results'][group],3)
        print('完成分组：' + group, flush=True)
    directory=ROOT / '.local/retrieval'
    directory.mkdir(parents=True,exist_ok=True)
    output=directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-rerank.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 固定8条候选重排，再选3条：离线对照','',
           '原始召回池完全沿用保存报告，不添加条目、不改变事实、不调用生成模型。分数不是事实支持证明。','',
           '| 分组 | 原前3条找全预期依据 | 重排后3条找全预期依据 |', '|---|---:|---:|']
    for group in ['development','mixed','targets']:
        rows=report['results'][group]
        positives=[row for row in rows if row['expected']]
        before=sum(set(row['expected']) <= set(row['baseline_ids']) for row in positives)
        after=report['summaries'][group]['all_expected_found']
        lines.append(f'| {group} | {before}/{len(positives)} | {after}/{len(positives)} |')
    for group, rows in report['results'].items():
        for row in rows:
            before=set(row['expected']) <= set(row['baseline_ids'])
            after=set(row['expected']) <= {h['id'] for h in row['hits']}
            if group not in ['mixed','targets','gaps'] and before == after:
                continue
            lines.extend(['','## '+row['id'], '',row['query'],'',
                          '原前3条：'+'、'.join(row['baseline_ids']),
                          '重排后3条：'+'、'.join(h['id'] for h in row['hits']),
                          '未进入原8条池：'+('、'.join(sorted(set(row['expected'])-set(row['candidate_ids']))) or '无'),
                          '在池中但筛掉：'+('、'.join(sorted((set(row['expected'])&set(row['candidate_ids']))-{h['id'] for h in row['hits']})) or '无')])
            for hit in row['hits']:
                lines.extend(['',f"- {hit['id']}，原名次{hit['retrieval_rank']}，重排分数{hit['score']:.4f}：{hit['facts']}"])
    lines.extend(['', '## 复核结论', '',
                  '单句收伞A01找回K12，多轮A02却筛掉K12；总数1/2不代表两个问法保持不变。', '',
                  '价格＋退款X01找回K11，但K20降到第7；X03筛掉预期K11，不过P05/P07商品条目也含56元价格，因此丢编号不能直接判定漏答价格。', '',
                  '伞柄材质缺口G04对“不锈钢伞骨”给出较高分，仍不能据此确认伞柄材质或设置自动回答阈值。', '',
                  '当前不替换默认检索：本地重排有单问收益，但跨诉求与多轮覆盖尚不稳定。下一步需保留当前问题与必要历史条件，并检查各诉求的依据覆盖。', '',
                  '模型快照：'+report['reranker_revision'], '',
                  '初始化耗时：'+str(report['setup_ms'])+'毫秒（单次本机测量）。','',
                  '无关题与参数/实时信息缺口仍需独立判定；找到预期条目不等于可直接回答。默认检索未替换，真实生成效果未测试。',''])
    output.with_suffix('.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(report['summaries'],ensure_ascii=False),flush=True)
    print('可阅读重排对照：'+str(output.with_suffix('.md')),flush=True)


if __name__ == '__main__':
    main()
