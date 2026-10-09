import argparse
import getpass
from pathlib import Path
import secrets
from . import config
from .db import SessionLocal
from .models import Merchant
from .security import password_hash

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='创建单商家账号，不覆盖已有账号。')
    parser.add_argument('--generate', action='store_true', help='随机生成本地密码并写入忽略目录')
    args = parser.parse_args()
    password = secrets.token_urlsafe(18) if args.generate else getpass.getpass('商家密码（至少12位）：')
    if len(password) < 12:
        raise SystemExit('至少12位。')
    with SessionLocal() as db:
        if db.get(Merchant, config.MERCHANT_USERNAME):
            raise SystemExit('账号已存在，不覆盖。')
        db.add(Merchant(username=config.MERCHANT_USERNAME, password_hash=password_hash(password)))
        db.commit()
    if args.generate:
        folder = Path(__file__).resolve().parents[2] / '.local'
        folder.mkdir(exist_ok=True)
        (folder / 'merchant-login.txt').write_text(f'账号：{config.MERCHANT_USERNAME}\n密码：{password}\n', encoding='utf-8')
    print('商家账号已创建，密码未显示。')
