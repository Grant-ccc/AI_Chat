"""Read saved v1 results offline; never repair rejected outputs or call a model."""
import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.answer_contract import GenerationContext, Proposal, check_proposal, unique_keys


def nest_valid_legacy(raw):
    """Mechanical conversion only: no text/status/action edits and no orphan removal."""
    if set(raw) != {'action', 'needs', 'claims', 'question', 'reason'}:
        raise ValueError('旧协议字段不匹配')
    if not isinstance(raw['needs'], list) or not isinstance(raw['claims'], list):
        raise ValueError('旧协议数组类型不正确')
    converted, referenced = [], set()
    for need in raw['needs']:
        if set(need) != {'subject', 'attribute', 'status', 'claim_indexes'}:
            raise ValueError('旧诉求字段不匹配')
        indexes = need['claim_indexes']
        if not isinstance(indexes, list) or any(type(i) is not int or not 0 <= i < len(raw['claims']) for i in indexes):
            raise ValueError('旧下标无效')
        if len(indexes) != len(set(indexes)):
            raise ValueError('旧下标重复')
        if (need['status'] == 'supported' and not indexes) or (need['status'] != 'supported' and indexes):
            raise ValueError('旧诉求与关联不一致，不能自动修复')
        referenced.update(indexes)
        converted.append({key: copy.deepcopy(value) for key, value in need.items() if key != 'claim_indexes'} |
                         dict(claims=[copy.deepcopy(raw['claims'][i]) for i in indexes]))
    if referenced != set(range(len(raw['claims']))):
        raise ValueError('旧回答有游离声明，不能删除或猜测归属')
    result = dict(schema_version=2, action=raw['action'], needs=converted,
                  question=raw['question'], reason=raw['reason'])
    Proposal.model_validate(result)
    return result


def replay_row(row):
    context = GenerationContext(**row['context'])
    response = row.get('response', {})
    result = dict(case_id=row['case_id'], question=row['question'],
                  original_validation=row.get('validation'), automatic_correction=False)
    if response.get('status') != 'complete' or response.get('finish_reason') != 'stop':
        return result | dict(conversion='skipped', reason='原调用未正常完成')
    try:
        raw = json.loads(response['content'], object_pairs_hook=unique_keys)
        converted = nest_valid_legacy(raw)
    except (ValueError, TypeError, KeyError):
        return result | dict(conversion='skipped_invalid_legacy',
                             reason='旧结构本身不合格；保留原始失败，不自动改动作、状态或声明归属')
    checked = check_proposal(json.dumps(converted, ensure_ascii=False), row['retrieval']['hits'], context, context)
    return result | dict(conversion='mechanical_nesting_only', converted_proposal=converted, validation=checked,
                         reason='原文、动作、状态及引用均未修改；不是新模型输出或事实支持证明')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', required=True, help='已保存的首题/六题JSON，可重复')
    args = parser.parse_args()
    rows = []
    for path in args.input:
        report = json.loads(path.read_text(encoding='utf-8'))
        for row in report.get('cases', [report]):
            rows.append(replay_row(row) | dict(source=str(path.resolve())))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / '.local/answer-contract'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (stamp + '-v2-replay.json')
    report = dict(mode='offline_legacy_replay', warning='只对原结构合格的旧回答做机械嵌套；不修复旧失败，不证明真实模型改善。', cases=rows)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 输出协议v2：历史回答离线复核', '', report['warning'], '',
             '| 原问题 | 处理 | 可直接发布 |', '|---|---|---|']
    for row in rows:
        label = '仅转换结构，仍待事实复核' if row['conversion'] == 'mechanical_nesting_only' else '保留失败，不自动修复'
        lines.append(f"| {row['question']} | {label} | 否 |")
    lines.extend(['', '旧数组下标错误不能靠删除声明、猜测归属或修改动作来掩盖。', '',
                  'v2真实生成表现尚未测试；收伞和混合诉求的检索漏检仍未解决。', ''])
    path.with_suffix('.md').write_text('\n'.join(lines), encoding='utf-8')
    print('复核' + str(len(rows)) + '份旧回答；无付费调用。')
    print('可阅读报告：' + str(path.with_suffix('.md')))


if __name__ == '__main__':
    main()
