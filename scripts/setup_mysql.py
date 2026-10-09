"""首次准备项目内MySQL；已有配置不覆盖，不注册系统服务。"""
import hashlib
from pathlib import Path
import subprocess
import sys
import socket
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1]
local = root / '.local'
local.mkdir(exist_ok=True)
base = local / 'mysql-8.4.11-winx64'
data = local / 'mysql-data'
if (root / 'backend/.env').exists():
    raise SystemExit('Local .env already exists; setup will not overwrite credentials or data.')
if data.exists():
    raise SystemExit('Data directory already exists. Inspect the previous setup; do not reinitialize it.')
try:
    with socket.create_connection(('127.0.0.1', 3307), timeout=1):
        raise SystemExit('Port 3307 already in use; no initialization attempted.')
except OSError:
    pass
archive = local / 'mysql.zip'
if not archive.exists():
    print('Downloading official MySQL 8.4.11 Windows archive...')
    urllib.request.urlretrieve('https://cdn.mysql.com/Downloads/MySQL-8.4/mysql-8.4.11-winx64.zip', archive)
with archive.open('rb') as stream:
    checksum = hashlib.file_digest(stream, 'md5').hexdigest()
if checksum != '2e833921898a9a030ea6bfe81bd811bc':
    raise SystemExit('Archive checksum does not match the official download. Inspect/remove the archive before retrying.')
if not (base / 'bin/mysqld.exe').exists():
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if not (local / member.filename).resolve().is_relative_to(local.resolve()):
                raise SystemExit('Unsafe archive path; extraction refused.')
        bundle.extractall(local)
data.mkdir()
with (local / 'mysql-init.log').open('ab') as log:
    subprocess.run(['bin\\mysqld.exe', '--no-defaults', '--initialize-insecure', '--basedir=.', '--datadir=../mysql-data'],
                   executable=str(base / 'bin/mysqld.exe'), cwd=base, stdout=log, stderr=log, check=True)
subprocess.run([sys.executable, str(root / 'scripts/start_mysql.py')], check=True)
subprocess.run([sys.executable, str(root / 'scripts/bootstrap_local_db.py')], check=True)
print('Project MySQL prepared. Initialize tables and create a merchant account next.')
