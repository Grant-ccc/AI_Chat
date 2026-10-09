"""v2 六题 × 三个模型复测；默认不联网，--run 才实际调用。"""
import argparse
from datetime import datetime, timezone
import json
import os
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        for model in MODELS:
            for test_id, question in QUESTIONS.items():
                payload = make_payload(model, question)
                assert payload['messages'][-1]['content'] == question
                assert 'M01' not in payload['messages'][0]['content']
                print(test_id, model, question)
        print('本地检查通过；probe-v2 实际运行将调用18次，包含重新测试T04。')
        return
    key = os.getenv('SDU_API_KEY')
    if not key:
        parser.exit(1, '未设置 SDU_API_KEY；请在原终端本地配置。\n')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'results' / ('batch-' + PROMPT_VERSION + '-' + stamp)
    folder.mkdir(parents=True)
    records = []
    for model in MODELS:
        for test_id, question in QUESTIONS.items():
            payload = make_payload(model, question)
            print(f'正在调用 {test_id} {model} ...', flush=True)
            req = urllib.request.Request(
                'https://xplt.sdu.edu.cn:4000/v1/chat/completions',
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
            record = {'test_id': test_id, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                      'prompt_version': PROMPT_VERSION, 'request': payload}
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
    print(f'18次调用完成。结果：{folder / "results.json"}；待人工复核，不自动评分。')


if __name__ == '__main__':
    main()
