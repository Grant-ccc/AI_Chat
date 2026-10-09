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
from sqlalchemy import create_engine, text

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--test-db', action='store_true', help='仅浏览器验收使用独立umbrella_test库')
args = parser.parse_args()
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
for port in (8000, 5173):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            raise SystemExit(f'Port {port} already in use; stop the previous dev server first.')
    except OSError:
        pass
local = root / '.local'
local.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONUTF8='1')
if args.test_db:
    env['DB_NAME'] = 'umbrella_test'
services = [
    ('api', [str(python), '-m', 'uvicorn', 'app.main:app', '--app-dir', str(root / 'backend'), '--host', '127.0.0.1', '--port', '8000']),
    ('web', [node, str(root / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1']),
]
processes = []
try:
    for name, command in services:
        with (local / f'{name}.log').open('ab') as log:
            process = subprocess.Popen(command, cwd=root / ('frontend' if name == 'web' else 'backend'),
                                       env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append((name, process))
    (local / 'dev-processes.json').write_text(json.dumps({name: process.pid for name, process in processes}), encoding='utf-8')
    deadline = time.monotonic() + 20
    for port in (8000, 5173):
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
print('Ready: http://localhost:5173 / http://localhost:5173/merchant')
if args.test_db:
    print('TEST DATABASE ONLY. Stop and restart without --test-db before normal use.')
