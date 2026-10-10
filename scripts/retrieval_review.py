"""生成可阅读的检索证据复核单；不调用模型，不自动判定能否回答。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows_by_id(rows):
    mapped = {row['id']: row for row in rows}
    if len(mapped) != len(rows):
        raise ValueError('报告中存在重复题号')
    return mapped


def compare_reports(baseline, candidate, method):
    # 除索引文本外，其余已记录设置必须相同；不接受不公平的前后对照。
    for key in ['knowledge_sha256', 'source_sha256', 'document_count', 'simulation_date',
                'model', 'embedding_versions', 'hybrid_parameters']:
        if baseline.get(key) != candidate.get(key):
            raise ValueError(f'对照条件不同：{key}')
    before = rows_by_id(baseline['results'][method])
    after = rows_by_id(candidate['results'][method])
    if before.keys() != after.keys():
        raise ValueError('前后题号不一致')
    result = []
    for id_, row in after.items():
        old = before[id_]
        if old['query'] != row['query'] or old['expected'] != row['expected']:
            raise ValueError(f'前后问题或预期依据不同：{id_}')
        if old['score_type'] != row['score_type']:
            raise ValueError(f'分数类型不同：{id_}')
        wanted = set(row['expected'])
        old_ids = {hit['id'] for hit in old['hits']}
        new_ids = {hit['id'] for hit in row['hits']}
        result.append(dict(id=id_, query=row['query'], expected=row['expected'],
                           before=old, after=row,
                           gained=sorted(wanted & (new_ids - old_ids)),
                           lost=sorted(wanted & (old_ids - new_ids)),
                           missing=sorted(wanted - new_ids), review_status='pending'))
    return result


def md(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


def evidence_lines(row):
    lines = []
    if not row['hits']:
        return ['没有检索候选。', '']
    for index, hit in enumerate(row['hits'], 1):
        source_names = '；'.join(f"{ref}：{source['name']}" for ref, source in hit['sources'].items())
        lines.extend([f"**候选{index}：{md(hit['id'])}｜{md(hit['topic'])}**", '',
                      f"事实：{md(hit['facts'])}", '',
                      f"回答边界：{md(hit['boundaries'])}", '',
                      f"适用轮次：{md(hit['round'])}；来源：{md(source_names)}", '',
                      f"排序分数：{hit['score']}（{md(row['score_type'])}，不代表可信度）", ''])
    return lines


def render_review(baseline, candidate, method='hybrid'):
    compared = compare_reports(baseline, candidate, method)
    for report in [baseline, candidate]:
        if method not in report.get('summaries', {}):
            raise ValueError('复核单需要开发题评测报告，不接受单次查询报告')
    before = baseline['summaries'][method]
    after = candidate['summaries'][method]
    if before['top_k'] != after['top_k']:
        raise ValueError('最终候选条数不同')
    lines = ['# 检索证据复核单', '',
             '这是构造开发题的诊断材料，不是客服回复，也不表示已通过事实支持检查。', '',
             f"方法：{method}；前{after['top_k']}条候选。基线：{baseline.get('index_text', 'facts')}；候选：{candidate.get('index_text', 'facts')}。", '',
             f"找全预期依据：{before['all_expected_found']}/{before['positive_cases']} → {after['all_expected_found']}/{after['positive_cases']}。", '',
             '预期编号是检索核对目标，不代表唯一可用资料；命中编号也不证明结论被支持。开发题预期仅用于此复核单，不进入索引或模型输入。', '',
             '## 前后变化', '',
             '| 题号 | 问题及已知上下文 | 新增预期依据 | 丢失预期依据 | 当前缺少 |',
             '|---|---|---|---|---|']
    for item in compared:
        lines.append('| ' + ' | '.join(md(v) for v in [item['id'], item['query'],
            ', '.join(item['gained']) or '—', ', '.join(item['lost']) or '—',
            ', '.join(item['missing']) or ('非相关题核对，不计算召回' if not item['expected'] else '—')]) + ' |')
    lines.extend(['', '## 逐题复核方法', '',
                  '1. 写出用户真正要确认的对象、属性或处理事项，结合已有上下文。',
                  '2. 找出能够支持这个结论的原文；型号、部件、轮次、时间口径必须一致。',
                  '3. 分开阅读事实与限制：“不得推断手柄材质”不能改写成“手柄是不锈钢”。',
                  '4. 标记缺少的是用户条件、知识事实、实时订单数据，还是必须由人工作出的决定。',
                  '5. 根据业务规则选择回答、追问、说明缺口或转人工；不要把所有资料缺失一律转人工，防晒检测资料按已确认规则直接说明。', '',
                  '下面只展开仍漏检或发生变化的开发题，以及补充资料缺口题。每题需要人工复核，工具不作通过判断。', ''])
    for item in compared:
        if not (item['gained'] or item['lost'] or item['missing']):
            continue
        lines.extend([f"## {item['id']}：{md(item['query'])}", '',
                      f"预期依据：{', '.join(item['expected'])}；当前缺少：{', '.join(item['missing']) or '无'}。", '',
                      '复核状态：待人工复核。所需事实、支持原文、缺口类型、建议动作均待填写。', ''])
        lines.extend(evidence_lines(item['after']))
    for row in candidate.get('gap_diagnostic', {}).get(method, []):
        lines.extend([f"## {md(row['id'])}：{md(row['query'])}", '',
                      f"已知限制：{md(row['limitation'])}", '',
                      '复核状态：待人工复核。候选可用于说明缺口，不能补造具体值。', ''])
        lines.extend(evidence_lines(row))
    lines.extend(['## 对照记录', '', f"知识文件哈希：{candidate['knowledge_sha256']}", '',
                  f"模型：{candidate.get('model')}；索引文本变化只使用既有公开字段。", '',
                  '此文件位于忽略目录，只用于当前复核。真实用户聊天、内部知识及凭据未写入此文件。', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--method', choices=['keyword', 'semantic', 'hybrid'], default='hybrid')
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    candidate = json.loads(args.candidate.read_text(encoding='utf-8'))
    content = render_review(baseline, candidate, args.method)
    directory = ROOT / '.local/retrieval'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-review.md')
    path.write_text(content, encoding='utf-8')
    print(f'已生成证据复核单：{path}')


if __name__ == '__main__':
    main()
