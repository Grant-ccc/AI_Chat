"""使用专用MySQL测试库，绝不重置应用库。"""
import os
import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, engine, get_db
from app.main import app
from app.security import password_hash, attempts
from app.models import Merchant
from app.init_db import ensure_schema


@pytest.fixture
def database(monkeypatch):
    name = os.getenv('TEST_DB_NAME', 'umbrella_test')
    if not name.endswith('_test') or name == engine.url.database:
        raise RuntimeError('只能使用独立且以_test结尾的MySQL测试库。')
    test_engine = create_engine(engine.url.set(database=name), pool_pre_ping=True, hide_parameters=True)
    ensure_schema(test_engine)
    with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    factory = sessionmaker(test_engine, expire_on_commit=False)
    from app import config, simple_ai, handoff_summary
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'disabled')
    monkeypatch.setattr(simple_ai, 'SessionLocal', factory)
    monkeypatch.setattr(handoff_summary, 'SessionLocal', factory)
    with factory() as db:
        db.add(Merchant(username='merchant', password_hash=password_hash('integration-password')))
        db.commit()
    def sessions():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    attempts.clear()
    yield factory
    app.dependency_overrides.clear()
    test_engine.dispose()
