from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from . import config


class Base(DeclarativeBase):
    pass


engine = create_engine(URL.create('mysql+pymysql', username=config.DB_USER,
    password=config.DB_PASSWORD, host=config.DB_HOST, port=config.DB_PORT,
    database=config.DB_NAME, query={'charset': 'utf8mb4'}), pool_pre_ping=True,
    connect_args={'connect_timeout': 5}, hide_parameters=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
