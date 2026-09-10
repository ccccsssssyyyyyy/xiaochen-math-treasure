import json
import os
import tempfile

# 让 source_normalize 在测试中加载仓库根的 example 占位映射，而非个人化的
# data/source_canonical_map.json（后者被 .gitignore 忽略，干净 clone 下不存在，
# 会导致来源归一测试失败）。必须在任何 mathbank.source_normalize 导入之前设置。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["MATHBANK_SOURCE_MAP"] = os.path.join(
    _REPO_ROOT, "source_canonical_map.example.json"
)

# ⚠️ MATHBANK_SOURCE_MAP 只是**只读**覆盖，绝不能当落盘目标。
# source_ai_fallback 会把 AI 学到的来源原子写回映射文件；若不重定向，测试就会
# 写进被提交的 source_canonical_map.example.json（历史上真的发生过：模板里
# 混入「某个全新来源ABC」等 4 条测试垃圾，还顺手 json.dumps 重排了整份文件）。
# 这里统一指到仓库外的临时目录，测试全程不碰任何真实映射。
_SCRATCH_MAP_PATH = os.path.join(
    tempfile.mkdtemp(prefix="mathbank-scratch-map-"), "source_canonical_map.json"
)
with open(_SCRATCH_MAP_PATH, "w", encoding="utf-8") as _f:
    json.dump({"school_entries": {}}, _f, ensure_ascii=False)
os.environ["MATHBANK_SOURCE_MAP_WRITE"] = _SCRATCH_MAP_PATH

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from mathbank import database
from mathbank.database import Base, Question

# Configure database module to use in-memory SQLite with StaticPool for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Patch the database module's engine and SessionLocal
database.engine = test_engine
database.SessionLocal = TestSessionLocal

# Patch sync_helper output paths globally during testing to avoid overwriting or clearing real data_backup files
from mathbank import sync_helper
test_backup_dir = tempfile.mkdtemp()
sync_helper.BACKUP_DIR = test_backup_dir
sync_helper.JSON_BACKUP_PATH = os.path.join(test_backup_dir, "questions_backup.json")
sync_helper.MD_BACKUP_PATH = os.path.join(test_backup_dir, "questions_library.md")

@pytest.fixture(scope="function")
def db_session():
    # Create all tables in the in-memory database
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function")
def client(db_session):
    from fastapi.testclient import TestClient
    from main import app
    from mathbank.database import get_db

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
