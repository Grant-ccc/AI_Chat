"""初始化空数据库的表；不创建数据库本身，不修改已有表结构。"""
from .db import Base, engine
from . import models  # 注册表

if __name__ == '__main__':
    Base.metadata.create_all(engine)
    print('数据库表初始化完成。')
