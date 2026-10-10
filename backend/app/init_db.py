"""创建应用表并执行保留已有数据的增量结构升级。"""
from sqlalchemy import inspect, text
from .db import Base, engine
from . import models  # 注册表

def ensure_schema(target):
    Base.metadata.create_all(target)
    columns = {column['name'] for column in inspect(target).get_columns('handoffs')}
    if 'viewed' not in columns:
        with target.begin() as connection:
            connection.execute(text('ALTER TABLE handoffs ADD COLUMN viewed BOOLEAN NOT NULL DEFAULT FALSE'))
    if 'ended_sequence' not in columns:
        with target.begin() as connection:
            connection.execute(text('ALTER TABLE handoffs ADD COLUMN ended_sequence INTEGER NULL'))


if __name__ == '__main__':
    ensure_schema(engine)
    print('数据库表初始化完成。')
