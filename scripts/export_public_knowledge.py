"""只从业务文档的公开表格导出检索数据；内部规则、测试答案不导出。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '花窗伞MVP_知识库与回答规则_v0.1.md'
OUTPUT = ROOT / 'knowledge/public.json'


def export(source=SOURCE):
    raw = source.read_bytes()
    rows = [tuple(cell.strip() for cell in line.strip().strip('|').split('|'))
            for line in raw.decode('utf-8-sig').splitlines() if line.startswith('|')]
    sources = {r[0]: {'name': r[1], 'scope': r[2]} for r in rows
               if re.fullmatch(r'S\d{2}', r[0])}
    entries = []
    products = []
    for row in rows:
        if re.fullmatch(r'K\d{2}', row[0]):
            if len(row) != 5:
                raise ValueError(f'公开知识表格式不符合预期：{row[0]}')
            refs = re.findall(r'S\d{2}', row[4])
            if not refs or any(ref not in sources for ref in refs):
                raise ValueError(f'来源缺失：{row[0]}')
            entries.append(dict(id=row[0], topic=row[1], round='二团',
                                facts=row[2], boundaries=row[3], visibility='public',
                                source_ids=refs, source_note=row[4]))
        elif re.fullmatch(r'P\d{2}', row[0]):
            if len(row) != 6:
                raise ValueError(f'商品表格式不符合预期：{row[0]}')
            products.append(dict(id=row[0], printing=row[1], window_position=row[2],
                                 outer_text=row[3], opening=row[4], price=row[5],
                                 topic='商品组合', round='二团', visibility='public',
                                 facts=f'{row[1]}，花窗在{row[2]}侧，外侧文字：{row[3]}，{row[4]}款，商品价格口径：{row[5]}。',
                                 boundaries='不表示含运费的订单总额，不添加未确认差价。',
                                 source_ids=['S02', 'S03']))
    return dict(schema_version=1, source_file=source.name,
                source_sha256=hashlib.sha256(raw).hexdigest(),
                business_round='二团', default_simulation_date='2026-05-04',
                sources=sources, entries=entries, products=products)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查已导出文件是否与源文档一致')
    args = parser.parse_args()
    data = export()
    if [e['id'] for e in data['entries']] != [f'K{i:02d}' for i in range(1, 21)]:
        raise ValueError('必须完整导出 K01—K20')
    if [p['id'] for p in data['products']] != [f'P{i:02d}' for i in range(1, 9)]:
        raise ValueError('必须完整导出 P01—P08')
    if args.check:
        if json.loads(OUTPUT.read_text(encoding='utf-8')) != data:
            raise SystemExit('公开知识导出已过期，请重新导出。')
        print('公开知识与源文档一致。')
    else:
        OUTPUT.parent.mkdir(exist_ok=True)
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('已导出20条公开知识和8种商品；未导出M/R规则及测试答案。')


if __name__ == '__main__':
    main()
