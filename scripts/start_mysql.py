"""以后台本地进程启动项目MySQL，避免Windows中文路径参数转义问题。"""
from pathlib import Path
import socket
import subprocess
import time

root = Path(__file__).resolve().parents[1]
base = root / '.local/mysql-8.4.11-winx64'
data = root / '.local/mysql-data'
try:
    with socket.create_connection(('127.0.0.1', 3307), timeout=1):
        raise SystemExit('3307 already listening; verify it is the project MySQL.')
except OSError:
    pass
if not (base / 'bin/mysqld.exe').exists() or not data.exists():
    raise SystemExit('Download and initialize MySQL first; see the local run guide.')
with (root / '.local/mysql.stdout.log').open('ab') as out, (root / '.local/mysql.stderr.log').open('ab') as err:
    process = subprocess.Popen(['bin\\mysqld.exe', '--no-defaults', '--basedir=.',
        '--datadir=../mysql-data', '--bind-address=127.0.0.1', '--port=3307', '--mysqlx=0'],
        executable=str(base / 'bin/mysqld.exe'), cwd=base, stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW)
for _ in range(40):
    if process.poll() is not None:
        raise SystemExit('MySQL exited; inspect .local/mysql.stderr.log.')
    try:
        with socket.create_connection(('127.0.0.1', 3307), timeout=1):
            print('MySQL ready on 127.0.0.1:3307.')
            break
    except OSError:
        time.sleep(.5)
else:
    raise SystemExit('MySQL not ready after 20s; inspect local logs before restarting.')
