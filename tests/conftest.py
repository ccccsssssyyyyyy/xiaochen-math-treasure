import json
import os
import sys
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


@pytest.fixture
def backup_sandbox(tmp_path, monkeypatch):
    """把 mathbank.backup 的所有落盘位置搬到临时目录。

    任何一个漏掉的重定向都会让测试写进用户真实的 data_backup/ —— 那是「跑一次
    测试就毁掉用户还原窗口」的经典事故。所以这里连 DATABASE_FILE 一起换掉，
    并在结束时断言真实的待还原请求文件没被创建。备份相关测试一律用它。
    """

    import mathbank.backup as backup_module
    from pathlib import Path

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    pre_restore = tmp_path / "pre_restore"
    data_backup = tmp_path / "data_backup"
    data_backup.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    database = tmp_path / "math_question_bank.db"
    pending = data_backup / "pending_restore.json"

    real_pending = Path(backup_module.PENDING_RESTORE_FILE)

    for name, value in (
        ("FULL_BACKUP_DIR", snapshots),
        ("PRE_RESTORE_BACKUP_DIR", pre_restore),
        ("DATA_BACKUP_DIR", data_backup),
        ("UPLOADS_DIR", uploads),
        ("DATABASE_FILE", database),
        ("PENDING_RESTORE_FILE", pending),
    ):
        monkeypatch.setattr(backup_module, name, Path(value))

    # main.py 只把 FULL_BACKUP_DIR 当展示字符串用，一并指到临时目录，
    # 免得接口返回的路径和实际落盘位置对不上。
    main_module = sys.modules.get("main")
    if main_module is not None:
        monkeypatch.setattr(main_module, "FULL_BACKUP_DIR", Path(snapshots))

    yield {
        "snapshots": snapshots,
        "pre_restore": pre_restore,
        "data_backup": data_backup,
        "uploads": uploads,
        "database": database,
        "pending": pending,
        "tmp": tmp_path,
    }

    assert not real_pending.exists(), (
        f"测试污染了真实待还原请求文件: {real_pending}"
    )
