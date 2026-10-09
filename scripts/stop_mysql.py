"""使用项目内保存的root凭据关闭本地MySQL，不删除数据。"""
from pathlib import Path
import pymysql
import ctypes
from ctypes import wintypes

root = Path(__file__).resolve().parents[1]
password_file = root / '.local/mysql-root-password.txt'
if not password_file.exists():
    raise SystemExit('Local root credential is missing; no shutdown attempted.')
connection = pymysql.connect(host='127.0.0.1', port=3307, user='root',
                             password=password_file.read_text(encoding='utf-8').strip(), connect_timeout=5)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
handle = None
try:
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@pid_file')
        # MySQL metadata can lose Chinese characters on Windows; use only its filename,
        # then verify the actual process image with the Unicode Windows API below.
        reported = Path(cursor.fetchone()[0])
        if reported.suffix != '.pid' or reported.parent.name != 'mysql-data':
            raise SystemExit('Unexpected server PID location; shutdown refused.')
        pid_file = root / '.local/mysql-data' / reported.name
        server_pid = int(pid_file.read_text(encoding='ascii').strip())
        handle = kernel.OpenProcess(0x00101000, False, server_pid)
        if not handle:
            raise SystemExit('Cannot observe server process termination; shutdown refused.')
        image_path = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image_path))
        expected = root / '.local/mysql-8.4.11-winx64/bin/mysqld.exe'
        if not kernel.QueryFullProcessImageNameW(handle, 0, image_path, ctypes.byref(length)) or Path(image_path.value).resolve() != expected.resolve():
            kernel.CloseHandle(handle)
            raise SystemExit('Server process is outside this project; shutdown refused.')
        cursor.execute('SHUTDOWN')
finally:
    connection.close()
try:
    if kernel.WaitForSingleObject(handle, 15000) != 0:
        raise SystemExit('Shutdown requested; process still closing. Inspect its error log before restarting.')
finally:
    kernel.CloseHandle(handle)
print('Local MySQL process stopped. Data retained.')
