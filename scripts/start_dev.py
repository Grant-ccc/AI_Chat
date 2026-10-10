"""启动本机开发服务；仅绑定回环地址，日志与进程编号保存在.local。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import sys
from urllib.parse import urlsplit
from sqlalchemy import create_engine, text

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--test-db', action='store_true', help='仅浏览器验收使用独立umbrella_test库')
parser.add_argument('--test-ai', action='store_true', help='配合--test-db使用明确标记的模拟AI，绝不调用官方模型')
parser.add_argument('--public-origin', help='朋友试用的HTTPS入口；提供构建产物并启用安全Cookie')
args = parser.parse_args()
if args.public_origin:
    address = urlsplit(args.public_origin)
    if args.test_db or address.scheme != 'https' or not address.hostname or address.username or address.password or address.path not in ('', '/') or address.query or address.fragment:
        parser.error('--public-origin必须是HTTPS来源地址，且不能与--test-db一起使用')
    args.public_origin = args.public_origin.rstrip('/')
    if not (root / 'frontend/dist/index.html').exists():
        parser.error('请先在frontend运行npm run build')
if args.test_ai and not args.test_db:
    parser.error('--test-ai只能与--test-db一起使用')
api_port, web_port = (8001, 5174) if args.test_db else (8000, 5173)
prefix = 'test-' if args.test_db else ''
python = root / '.venv/Scripts/python.exe'
node = shutil.which('node')
if not python.exists() or not node:
    raise SystemExit('Install the Python environment and Node.js first.')
sys.path.insert(0, str(root / 'backend'))
from app.db import engine
check = create_engine(engine.url.set(database='umbrella_test' if args.test_db else engine.url.database), hide_parameters=True)
try:
    with check.connect() as connection:
        connection.execute(text('SELECT 1'))
except Exception:
    raise SystemExit('Database connection failed. Start MySQL and check backend/.env first.') from None
finally:
    check.dispose()
for port in (api_port, web_port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            raise SystemExit(f'Port {port} already in use; stop the previous dev server first.')
    except OSError:
        pass
local = root / '.local'
local.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONUTF8='1')
if args.public_origin:
    env['APP_ORIGIN'] = args.public_origin
    env['COOKIE_SECURE'] = 'true'
    env['PUBLIC_APP_ORIGIN'] = args.public_origin
if args.test_db:
    env['DB_NAME'] = 'umbrella_test'
    env['APP_ORIGIN'] = f'http://localhost:{web_port}'
    env['AI_WEB_MODE'] = 'mock' if args.test_ai else 'disabled'
env['API_TARGET'] = f'http://127.0.0.1:{api_port}'
services = [
    ('api', [str(python), '-m', 'uvicorn', 'app.main:app', '--app-dir', str(root / 'backend'), '--host', '127.0.0.1', '--port', str(api_port)]),
    ('web', [node, str(root / 'frontend/node_modules/vite/bin/vite.js'), *(['preview'] if args.public_origin else []), '--host', '127.0.0.1', '--port', str(web_port)]),
]
processes = []
try:
    for name, command in services:
        with (local / f'{prefix}{name}.log').open('ab') as log:
            process = subprocess.Popen(command, cwd=root / ('frontend' if name == 'web' else 'backend'),
                                       env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append((name, process))
    (local / f'{prefix}dev-processes.json').write_text(json.dumps({name: process.pid for name, process in processes}), encoding='utf-8')
    deadline = time.monotonic() + 20
    for port in (api_port, web_port):
        while True:
            if any(process.poll() is not None for _, process in processes):
                raise RuntimeError('Service exited; inspect .local/api.log and .local/web.log.')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.5):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Startup timed out; inspect local logs.')
                time.sleep(.2)
except Exception:
    for _, process in processes:
        if process.poll() is None:
            process.terminate()
    raise
print(f'Ready: http://localhost:{web_port} / http://localhost:{web_port}/merchant')
if args.public_origin:
    print(f'External trial: {args.public_origin} / {args.public_origin}/merchant (use HTTPS for login)')
if args.test_db:
    print('TEST DATABASE ONLY. Normal services on 5173 are separate; browser tests use isolated contexts.')
