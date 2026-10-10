"""Prepare, or explicitly run, one official DeepSeek connectivity/evidence smoke test."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.answer_contract import GenerationContext, build_deepseek_request, check_proposal
from app.deepseek_probe import CallLedger, ProbeSettings, prepare_payload, send_once
from app.retrieval import HybridRetriever, KeywordRetriever, SemanticRetriever, load_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='明确发送1次付费请求；默认只检查配置与准备')
    args = parser.parse_args()
    try:
        settings = ProbeSettings.load(ROOT / 'backend/.env')
        data, documents = load_documents(ROOT / 'knowledge/public.json')
        policy = json.loads((ROOT / 'knowledge/answer-policy.json').read_text(encoding='utf-8'))
        context = GenerationContext(status='ai_ready', revision=0, latest_user_sequence=1,
                                    business_round=data['business_round'], simulation_date=data['default_simulation_date'])
        question = '防晒伞下雨能用吗？'
        semantic = SemanticRetriever(documents, ROOT / '.local/embedding-models',
                                     model='BAAI/bge-small-zh-v1.5', local_only=True)
        retrieval = HybridRetriever(KeywordRetriever(documents), semantic).search(question)
        request = build_deepseek_request(context, question, [], retrieval['hits'], policy, settings.model)
        payload, allowance, reserve = prepare_payload(request, settings)
        directory = ROOT / '.local/deepseek-test'
        ledger = CallLedger(directory / 'approved-seven-calls.sqlite3')
        report = dict(mode='paid_smoke_test' if args.run else 'dry_run', case_id='smoke-rain',
                      question=question, model=settings.model, context=context.model_dump(),
                      knowledge_sha256=data['source_sha256'], policy_sha256=hashlib.sha256(
                          (ROOT / 'knowledge/answer-policy.json').read_bytes()).hexdigest(),
                      retrieval=retrieval, request=payload, input_token_allowance=allowance,
                      reserved_cny=str(reserve), ledger_before=ledger.snapshot())
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = directory / (stamp + '.json')
        # Save the public request before transmitting; never save Authorization.
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.run:
            result = send_once(request, settings, ledger, report['case_id'])
            report['response'] = result
            if result['status'] == 'complete':
                report['validation'] = check_proposal(result['content'], retrieval['hits'], context, context,
                                                       result['finish_reason'])
            report['ledger_after'] = ledger.snapshot()
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('调用结果：' + result['status'])
            if 'validation' in report:
                print('引用校验：' + report['validation']['status'] + '；仍需人工复核，不发布到聊天。')
        else:
            print('配置检查通过，仅准备请求；没有发送。')
        print('本题费用预留：' + str(reserve) + '元；失败或未知结果不会释放预留。')
        print('本地报告：' + str(path))
    except Exception as error:
        # Runtime/third-party errors can contain input values; never echo raw errors.
        print('准备或执行失败：' + type(error).__name__ + '；未自动重试。请检查配置、模型缓存和调用记录。')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
