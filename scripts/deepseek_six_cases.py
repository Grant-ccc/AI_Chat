"""Six fixed development cases sharing the previously approved seven-call ledger."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.answer_contract import GenerationContext, build_deepseek_request, check_proposal
from app.deepseek_probe import CallLedger, ProbeSettings, prepare_payload, send_once
from app.retrieval import HybridRetriever, KeywordRetriever, SemanticRetriever, load_documents

# Development/gap cases only. No held-out questions or expected answers enter requests.
CASES = [
    dict(id='six-material', category='正常回答', question='我想知道材质，布是什么，骨架是什么？'),
    dict(id='six-upf', category='资料缺失例外', question='有 UPF50+ 的报告吗？'),
    dict(id='six-clarify', category='条件不明追问', question='伞怎么收回去？'),
    dict(id='six-exchange', category='人工处理', question='收到就断了，没录开箱，只有刚拍的照片，给我换一把。'),
    dict(id='six-mixed', category='混合诉求', question='二团单面款多少钱？我收到的伞坏了，想退款。'),
    dict(id='six-handle', category='部件混淆', question='伞柄具体是什么材质？是不锈钢吗？'),
]


def prepare_cases(data, retriever, policy, settings):
    context = GenerationContext(status='ai_ready', revision=0, latest_user_sequence=1,
                                business_round=data['business_round'], simulation_date=data['default_simulation_date'])
    rows = []
    for case in CASES:
        retrieval = retriever.search(case['question'])
        request = build_deepseek_request(context, case['question'], [], retrieval['hits'], policy, settings.model)
        payload, allowance, reserved = prepare_payload(request, settings)
        rows.append(dict(case_id=case['id'], category=case['category'], question=case['question'],
                         context=context.model_dump(), retrieval=retrieval, request=payload,
                         input_token_allowance=allowance, reserved_cny=str(reserved)))
    return rows


def preflight(rows, ledger, settings):
    snapshot = ledger.snapshot()
    total = Decimal(snapshot['reserved_cny']) + sum((Decimal(row['reserved_cny']) for row in rows), Decimal('0'))
    if snapshot['blocked'] or snapshot['calls'] + len(rows) > settings.max_calls or total > settings.budget:
        raise ValueError('本轮剩余次数、费用或调用状态不允许运行整组；不自动补测或重试')
    return snapshot, total


def save_report(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run_cases(rows, settings, ledger, persist, sender=send_once):
    """Save after each call; stop on transport errors but retain invalid model outputs for review."""
    for row in rows:
        result = sender(row['request'], settings, ledger, row['case_id'])
        row['response'] = result
        if result['status'] == 'complete':
            context = GenerationContext(**row['context'])
            row['validation'] = check_proposal(result['content'], row['retrieval']['hits'], context, context,
                                               result['finish_reason'])
        persist()
        print(row['category'] + '：' + result['status'] + '；' + row.get('validation', {}).get('status', '未校验'), flush=True)
        if result['status'] != 'complete':
            break


def markdown(report):
    lines = ['# DeepSeek官方接口：六题结果', '',
             '固定开发案例；同一份知识、修订后的提示、混合检索前3条、非思考模式。单次样本不是稳定正确率。', '',
             '| 类别 | HTTP结果 | 结构/引用校验 | 耗时（秒） | 输入/输出tokens | 高峰费用估算（元） |',
             '|---|---|---|---:|---|---:|']
    for row in report['cases']:
        result = row.get('response', {})
        usage = result.get('usage', {})
        lines.append(f"| {row['category']} | {result.get('status', '未调用')} | "
                     f"{row.get('validation', {}).get('status', '未校验')} | {result.get('elapsed_seconds', '')} | "
                     f"{usage.get('prompt_tokens', '')}/{usage.get('completion_tokens', '')} | "
                     f"{result.get('peak_cost_upper_estimate_cny', '')} |")
    for row in report['cases']:
        lines.extend(['', '## ' + row['category'], '', '问题：' + row['question'], '',
                      '候选编号：' + '、'.join(hit['id'] for hit in row['retrieval']['hits']), '',
                      '事实支持、诉求覆盖与业务动作：待人工复核，不能自动发布。', '', '```json',
                      row.get('response', {}).get('content', '未获得候选回答'), '```'])
    lines.extend(['', '本报告不包含Key、Authorization或商家内部资料；平台实际扣费未查询。', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='发送剩余6题；默认仅准备，无付费请求')
    args = parser.parse_args()
    try:
        settings = ProbeSettings.load(ROOT / 'backend/.env')
        data, documents = load_documents(ROOT / 'knowledge/public.json')
        policy_path = ROOT / 'knowledge/answer-policy.json'
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        semantic = SemanticRetriever(documents, ROOT / '.local/embedding-models', local_only=True)
        retriever = HybridRetriever(KeywordRetriever(documents), semantic)
        rows = prepare_cases(data, retriever, policy, settings)
        directory = ROOT / '.local/deepseek-test'
        ledger = CallLedger(directory / 'approved-seven-calls.sqlite3')
        snapshot, total = preflight(rows, ledger, settings)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = directory / (stamp + '-six.json')
        report = dict(mode='paid_six_cases' if args.run else 'dry_run', cases=rows,
                      knowledge_sha256=data['source_sha256'], policy_sha256=hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                      prompt_code_sha256=hashlib.sha256((ROOT / 'backend/app/answer_contract.py').read_bytes()).hexdigest(),
                      model=settings.model, ledger_before=snapshot, whole_round_reserved_cny=str(total))
        def persist():
            report['ledger_after'] = ledger.snapshot()
            save_report(path, report)
        persist()
        print('整轮7题预留合计：' + str(total) + '元；本轮已有' + str(snapshot['calls']) + '次记录。', flush=True)
        if args.run:
            run_cases(rows, settings, ledger, persist)
        path.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
        print('本地报告：' + str(path), flush=True)
        if not args.run:
            print('只准备请求，没有发送。')
    except Exception as error:
        print('准备或执行失败：' + type(error).__name__ + '；未自动重试。请检查配置和调用记录。')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
