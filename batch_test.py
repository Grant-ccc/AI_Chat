"""v2 六题 × 三个模型复测；默认不联网，--run 才实际调用。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from test_model import ROOT, PROMPT_VERSION, make_payload

MODELS = ['SDU-AI/DeepSeek-V4-Flash', 'Ali-dashscope/Qwen3.5-Flash',
          'Ali-dashscope/Qwen3.5-Plus']
QUESTIONS = {
    'T01': '防晒伞下雨能用吗？',
    'T04': '有 UPF50+ 的报告吗？',
    'T07': '我要双面花窗在外面的那款。',
    'T10': '伞怎么收回去？',
    'T15': '二团售后多久，从签收开始算吗？',
    'T17': '收到就断了，没录开箱，只有刚拍的照片，给我换一把。',
}


def run_batch(models, questions, label='batch', histories=None, repetitions=1):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', type=Path, help='读取本批旧results.json，仅跳过已有文本回复的调用')
    args = parser.parse_args()
    histories = histories or {}
    items = []
    for sample in range(1, repetitions + 1):
        for model in models:
            for test_id, question in questions.items():
                payload = make_payload(model, question)
                payload['messages'][1:1] = histories.get(test_id, [])
                items.append((test_id, model, payload, sample))
    count = len(items)
    completed = []
    if args.resume:
        source = args.resume.resolve()
        if not source.is_relative_to((ROOT / 'results').resolve()):
            parser.error('续测文件必须位于本项目results目录。')
        try:
            previous = json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(previous, list):
                raise ValueError('记录必须为列表')
            planned = {(i, m, s): p for i, m, p, s in items}
            seen = set()
            for record in previous:
                identity = (record['test_id'], record['request']['model'], record.get('sample_index', 1))
                if identity in seen or record['prompt_version'] != PROMPT_VERSION or planned.get(identity) != record['request']:
                    raise ValueError('旧记录与当前题目、提示或参数不一致，或有重复记录')
                seen.add(identity)
                choices = record.get('response', {}).get('choices')
                reply = choices[0].get('message', {}).get('content') if isinstance(choices, list) and choices else None
                if 'error' not in record and isinstance(reply, str) and reply.strip():
                    completed.append(dict(record, resume_source=str(source)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            parser.error(f'无法安全续测：{exc}')
        done = {(r['test_id'], r['request']['model'], r.get('sample_index', 1)) for r in completed}
        items = [item for item in items if (item[0], item[1], item[3]) not in done]
        print(f'保留已完成{len(completed)}次，仅补测{len(items)}次。')
    if not args.run:
        for test_id, model, payload, sample in items:
            assert payload['messages'][-1]['content'] == questions[test_id]
            assert 'M01' not in payload['messages'][0]['content']
            print(test_id, model, f'第{sample}次', json.dumps(payload['messages'][1:], ensure_ascii=False))
        print(f'本地检查通过；{PROMPT_VERSION} 实际运行将调用{len(items)}次，本批总计{count}次。')
        return
    if not items:
        print('全部已有文本结果，无需再次调用。')
        return
    key = os.getenv('SDU_API_KEY')
    if not key:
        parser.exit(1, '未设置 SDU_API_KEY；请在原终端本地配置。\n')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'results' / (label + '-' + PROMPT_VERSION + '-' + stamp)
    folder.mkdir(parents=True)
    records = completed.copy()
    for test_id, model, payload, sample in items:
        print(f'正在调用 {test_id} {model} 第{sample}次 ...', flush=True)
        req = urllib.request.Request(
            'https://xplt.sdu.edu.cn:4000/v1/chat/completions',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        record = {'test_id': test_id, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                  'prompt_version': PROMPT_VERSION, 'sample_index': sample, 'request': payload}
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                data = json.load(response)
            record['response'] = data
        except urllib.error.HTTPError as exc:
            record['error'] = f'HTTP {exc.code}；检查模型、密钥或参数。'
        except (urllib.error.URLError, TimeoutError):
            record['error'] = '连接失败或超时；检查校园网/VPN。'
        except ValueError:
            record['error'] = '响应不是有效JSON。'
        record['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        records.append(record)
        (folder / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        if 'error' in record:
            parser.exit(1, f"{record['error']} 已保留完成记录并停止；不自动重试。结果：{folder}\n")
        try:
            choice = data['choices'][0]
            reply = choice['message'].get('content')
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError('没有可用文本回复')
            print(json.dumps({'reply': reply, 'finish_reason': choice.get('finish_reason'),
                              'elapsed_seconds': record['elapsed_seconds']}, ensure_ascii=False), flush=True)
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            parser.exit(1, f'返回结构异常或回复为空，停止调用并保留原始记录：{folder}\n')
    print(f'{count}次调用完成。结果：{folder / "results.json"}；待人工复核，不自动评分。')


if __name__ == '__main__':
    run_batch(MODELS, QUESTIONS)
