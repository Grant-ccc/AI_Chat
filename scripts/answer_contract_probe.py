"""离线演示输出校验或准备请求体；没有发送请求、读取Key或写聊天数据库的能力。"""
import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.answer_contract import GenerationContext, build_deepseek_request, check_proposal, generation_route
from app.retrieval import KeywordRetriever, load_documents


def sample_proposal(doc, text, subject, attribute):
    return dict(schema_version=2, action='answer', needs=[dict(subject=subject, attribute=attribute,
                status='supported', claims=[dict(kind='fact', subject=subject,
                attribute=attribute, text=text, evidence=[dict(knowledge_id=doc['id'], field='facts', quote=doc['facts'])])])],
                question=None, reason=None)


def run_demo(documents, context):
    docs = {doc['id']: doc for doc in documents}
    rain = sample_proposal(docs['K02'], '二团晴雨伞可以在雨天正常使用。', '二团晴雨伞', '雨天使用')
    false_handle = sample_proposal(docs['K05'], '伞柄是不锈钢。', '伞柄', '材质')
    report = sample_proposal(docs['K06'], '目前没有可提供的防晒检测报告，无法确认具体指标。', '防晒报告', '可提供情况')
    fabricated = copy.deepcopy(rain)
    fabricated['needs'][0]['claims'][0]['evidence'][0]['quote'] = '能够抵抗十二级风。'
    unknown = copy.deepcopy(rain)
    unknown['needs'][0]['claims'][0]['evidence'][0]['knowledge_id'] = 'K99'
    mixed = copy.deepcopy(rain)
    mixed['action'], mixed['reason'] = 'handoff', '换货需要商家处理。'
    mixed['needs'].append(dict(subject='用户申请', attribute='换货', status='human_decision', claims=[]))
    reminder = sample_proposal(docs['K19'], '请准备完整订单截图和清晰瑕疵照片。', '售后申请', '处理')
    reminder['action'], reminder['reason'] = 'handoff', '申请需要商家判断，材料提醒不代表获批。'
    reminder['needs'][0]['status'] = 'human_decision'
    clarify = dict(schema_version=2, action='clarify', needs=[dict(subject='商品', attribute='开合方式',
                   status='missing_user', claims=[])], question='请问是自动款还是手动款？', reason=None)
    outputs = []
    for title, proposal, selected, current, finish in [
        ('雨天使用：引用真实，但事实与业务动作仍需复核', rain, [docs['K02']], context, 'stop'),
        ('伪造抗风原文：应拒绝', fabricated, [docs['K02']], context, 'stop'),
        ('编造知识编号：应拒绝', unknown, [docs['K02']], context, 'stop'),
        ('真实伞骨引用却说成伞柄：不能当作已证实', false_handle, [docs['K05']], context, 'stop'),
        ('防晒资料缺失：候选可直接说明，不强制转人工', report, [docs['K06']], context, 'stop'),
        ('用户已转人工：旧输出应丢弃', rain, [docs['K02']],
            GenerationContext(**(context.model_dump() | dict(status='waiting_human', revision=context.revision+1))), 'stop'),
        ('模型输出被截断：应拒绝', rain, [docs['K02']], context, 'length'),
        ('混合诉求：回答已知部分并保留待人工申请', mixed, [docs['K02']], context, 'stop'),
        ('转人工时附材料提醒：仍未决定申请结果', reminder, [docs['K19']], context, 'stop'),
        ('缺少用户条件：追问一个条件', clarify, [], context, 'stop'),
    ]:
        result = check_proposal(json.dumps(proposal, ensure_ascii=False), selected, context, current, finish)
        outputs.append(dict(case=title, input_proposal=proposal, result=result))
    return dict(mode='synthetic_offline_demo', warning='全部为手工构造样例，不是真实模型回答或效果评测。', cases=outputs)


def demo_markdown(report):
    labels = {'citation_checked_pending_review': '引用可追溯，待事实复核',
              'rejected': '拒绝', 'discarded': '丢弃旧结果'}
    lines = ['# 模型输出协议：离线演示', '', report['warning'], '',
             '| 构造场景 | 校验结果 | 可直接发送给用户 |', '|---|---|---|']
    for case in report['cases']:
        result = case['result']
        lines.append(f"| {case['case']} | {labels[result['status']]} | 否 |")
    lines.extend(['', '## 怎样理解结果', '',
                  '引用检查只证明编号在本次候选中、原文来自指定的事实或边界字段。', '',
                  '例如“伞骨为不锈钢”确实存在，但不能支持“伞柄是不锈钢”。这个例子仍待事实支持复核，不能发布。', '',
                  '后续需要分别验证：是否覆盖全部诉求、原文是否支持具体结论、时间/部件/型号是否一致，以及动作是否符合业务规则。', '',
                  '本步未接入真实模型或聊天页面；没有执行退款、转交或数据库写入。', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-query', help='准备此问题的请求JSON，不发送；无此参数时演示构造样例')
    parser.add_argument('--model', help='准备请求时需明确提供官方模型ID，无默认猜测')
    parser.add_argument('--status', choices=['ai_ready', 'waiting_human', 'human_active', 'ended'], default='ai_ready')
    parser.add_argument('--explicit-handoff', action='store_true', help='模拟用户主动要求人工')
    parser.add_argument('--context', action='append', default=[], help='已知公开用户消息，可重复')
    args = parser.parse_args()
    if args.prepare_query is not None and (not args.prepare_query.strip() or not args.model or not args.model.strip()):
        parser.error('准备请求需提供非空 --prepare-query 和 --model')
    if args.prepare_query is None and (args.model or args.context or args.explicit_handoff or args.status != 'ai_ready'):
        parser.error('情境参数用于 --prepare-query；默认演示使用固定构造情境')
    data, documents = load_documents(ROOT / 'knowledge/public.json')
    context = GenerationContext(status=args.status, revision=0, latest_user_sequence=1,
                                business_round=data['business_round'], simulation_date=data['default_simulation_date'],
                                explicit_handoff=args.explicit_handoff)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / '.local/answer-contract'
    directory.mkdir(parents=True, exist_ok=True)
    if args.prepare_query is not None:
        route = generation_route(context)
        if route != 'generate':
            report = dict(mode='offline_request_preparation', route=route, request=None, context=context.model_dump())
        else:
            policy = json.loads((ROOT / 'knowledge/answer-policy.json').read_text(encoding='utf-8'))
            query = '\n'.join(args.context + [args.prepare_query])
            hits = KeywordRetriever(documents).search(query)['hits']
            request = build_deepseek_request(context, args.prepare_query,
                [dict(role='user', content=text) for text in args.context], hits, policy, args.model)
            report = dict(mode='offline_request_preparation', route=route, request=request,
                          context=context.model_dump(), knowledge_sha256=data['source_sha256'],
                          warning='仅准备请求体，不发送。此例使用字面检索，不能据此判断资料充分。')
    else:
        report = run_demo(documents, context)
        markdown = directory / (stamp + '-demo.md')
        markdown.write_text(demo_markdown(report), encoding='utf-8')
        for case in report['cases']:
            print(f"{case['case']} -> {case['result']['status']}；可发布=False")
        print(f'可阅读演示：{markdown}')
    path = directory / (stamp + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'离线结果：{path}；未发送模型请求。')


if __name__ == '__main__':
    main()
