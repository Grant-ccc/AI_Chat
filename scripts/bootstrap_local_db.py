"""仅用于首次初始化项目内空密码MySQL；设置随机密码，不输出凭据。"""
from pathlib import Path
import secrets
import pymysql

root = Path(__file__).resolve().parents[1]
env = root / 'backend' / '.env'
if env.exists():
    raise SystemExit('本地配置已存在，不覆盖。')
app_password = secrets.token_hex(24)
root_password = secrets.token_hex(24)
with pymysql.connect(host='127.0.0.1', port=3307, user='root', password='', autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute('CREATE DATABASE IF NOT EXISTS umbrella CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        cur.execute('CREATE DATABASE IF NOT EXISTS umbrella_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        cur.execute("CREATE USER 'umbrella'@'localhost' IDENTIFIED BY %s", (app_password,))
        cur.execute("GRANT ALL ON umbrella.* TO 'umbrella'@'localhost'")
        cur.execute("GRANT ALL ON umbrella_test.* TO 'umbrella'@'localhost'")
        cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (root_password,))
env.write_text(f'DB_HOST=127.0.0.1\nDB_PORT=3307\nDB_NAME=umbrella\nDB_USER=umbrella\nDB_PASSWORD={app_password}\nAPP_ORIGIN=http://localhost:5173\nCOOKIE_SECURE=false\nMERCHANT_USERNAME=merchant\n', encoding='utf-8')
(root / '.local' / 'mysql-root-password.txt').write_text(root_password, encoding='utf-8')
print('已设置随机root密码、专用数据库账号和本地配置；凭据未输出。')
