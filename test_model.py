"""首次探测：默认不联网；--run 才调用学校 API 一次。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
MODEL = 'SDU-AI/DeepSeek-V4-Flash'
PROMPT_VERSION = 'probe-v2'


def make_payload(model=MODEL, question='有 UPF50+ 的报告吗？'):
    text = (ROOT / '花窗伞MVP_知识库与回答规则_v0.1.md').read_text(encoding='utf-8-sig')
    public = text.split('## 一、来源与采用顺序', 1)[1].split('## 四、仅商家可见知识', 1)[0]
    rules = text.split('## 五、回答与交接规则', 1)[1].split('## 六、内部摘要模板', 1)[0]
    prompt_doc = (ROOT / '模型评测_公共提示词_v2_审阅稿.md').read_text(encoding='utf-8-sig')
    prompt = prompt_doc.split('## 拟使用的系统提示', 1)[1].split('以下附公开知识与回答规则：', 1)[0].strip()
    return {
        'model': model,
        'messages': [
            {'role': 'system', 'content': prompt + '\n以下附公开知识与回答规则：\n' + public + '\n' + rules},
            {'role': 'user', 'content': question},
        ],
        'stream': False,
        'max_tokens': 1000,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='实际调用一次，会消耗学校额度')
    parser.add_argument('--model', default=MODEL, help='学校模型页复制的准确模型 ID')
    args = parser.parse_args()
    payload = make_payload(args.model)
    if not args.run:
        print('本地检查通过：T04，模型 ' + args.model + '；未发起网络请求。')
        return
    key = os.getenv('SDU_API_KEY')
    if not key:
        parser.exit(1, '未设置 SDU_API_KEY；请在本地配置，不要发送到聊天。\n')
    request = urllib.request.Request(
        'https://xplt.sdu.edu.cn:4000/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        parser.exit(1, f'HTTP {exc.code}；检查密钥、模型和接口参数。未自动重试。\n')
    except (urllib.error.URLError, TimeoutError):
        parser.exit(1, '连接失败或超时；检查校园网/VPN。未自动重试。\n')
    except ValueError:
        parser.exit(1, '接口未返回有效 JSON。未自动重试。\n')
    elapsed = round(time.perf_counter() - started, 3)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'results'
    folder.mkdir(exist_ok=True)
    path = folder / f'T04-{stamp}.json'
    record = {'test_id': 'T04', 'timestamp_utc': stamp, 'prompt_version': PROMPT_VERSION,
              'request': payload, 'response': data, 'elapsed_seconds': elapsed}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        reply = data['choices'][0]['message'].get('content')
        finish_reason = data['choices'][0].get('finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        parser.exit(1, f'响应结构与预期不同，原始结果已保存：{path}\n')
    print(json.dumps({'reply': reply, 'finish_reason': finish_reason,
                      'usage': data.get('usage'), 'elapsed_seconds': elapsed,
                      'saved_to': str(path)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
