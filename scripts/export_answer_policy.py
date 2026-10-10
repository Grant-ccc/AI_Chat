"""单独导出公开回答规则；不导出商家规则或测试答案，不进入检索索引。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '花窗伞MVP_知识库与回答规则_v0.1.md'
OUTPUT = ROOT / 'knowledge/answer-policy.json'


def export_policy():
    raw = SOURCE.read_bytes()
    rules = []
    for line in raw.decode('utf-8-sig').splitlines():
        if not line.startswith('| R'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 2 or not re.fullmatch(r'R\d{2}', cells[0]):
            raise ValueError('回答规则表格式不符合预期')
        rules.append(dict(id=cells[0], text=cells[1]))
    if [rule['id'] for rule in rules] != [f'R{i:02d}' for i in range(1, 11)]:
        raise ValueError('必须完整导出R01—R10')
    return dict(schema_version=1, visibility='public', source_file=SOURCE.name,
                source_sha256=hashlib.sha256(raw).hexdigest(), rules=rules)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    data = export_policy()
    if args.check:
        if json.loads(OUTPUT.read_text(encoding='utf-8')) != data:
            raise SystemExit('回答规则导出已过期，请重新导出。')
        print('回答规则与源文档一致。')
    else:
        OUTPUT.parent.mkdir(exist_ok=True)
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('已单独导出10条公开回答规则，未导出M规则及测试答案。')


if __name__ == '__main__':
    main()
