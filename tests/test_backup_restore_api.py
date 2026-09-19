"""备份与还原的 HTTP 层行为（TestClient 真实过一遍中间件与路由）。

这里要证明的核心不是"接口能返回 200"，而是**破坏性动作的护栏真的在**：

* 没有二次确认就不能登记还原；
* 校验不过的快照不能进队列；
* 登记请求**绝不改动当前数据**（这才是"延迟"的含义）；
* 取消之后重启不会再动数据；
* 新写的写接口也受本地令牌保护，不是敞开的后门。

隔离由 conftest 的 ``backup_sandbox`` 保证：所有落盘位置都指向 tmp_path，
夹具退出时还会断言用户真实的待还原请求文件没被创建。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import mathbank.backup as backup_module
from main import LOCAL_TOKEN
from mathbank.backup import PENDING_RESTORE_FORMAT, apply_pending_restore, create_full_backup

AUTH = {"X-Local-Token": LOCAL_TOKEN}

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA user_version=1;
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    answer_markdown TEXT,
    image_paths TEXT
);
CREATE TABLE question_curriculums (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    version_code TEXT NOT NULL,
    UNIQUE(question_id, version_code)
);
CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE paper_questions (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL,
    UNIQUE(paper_id, order_index)
);
"""


def _make_database(path: Path, question_count: int, marker: str) -> None:
    """造一个能被 verify_full_backup 认下的最小数据库（先清掉同名旧库）。"""

    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        for question_id in range(1, question_count + 1):
            connection.execute(
                "INSERT INTO questions (id, content, answer_markdown, image_paths)"
                " VALUES (?, ?, ?, ?)",
                (question_id, f"{marker}-question-{question_id}", None, "[]"),
            )
            connection.execute(
                "INSERT INTO question_curriculums (id, question_id, version_code)"
                " VALUES (?, ?, ?)",
                (question_id, question_id, "A"),
            )
        connection.commit()


def _question_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0])


def _make_snapshot(backup_sandbox) -> Path:
    return create_full_backup(
        output_dir=backup_sandbox["snapshots"],
        database_path=backup_sandbox["database"],
        uploads_dir=backup_sandbox["uploads"],
        metadata_path=backup_sandbox["data_backup"] / "custom_metadata.json",
        retention=None,
    )


def _write_junk_archive(directory: Path, name: str) -> Path:
    """一个命名合规、内容不合规的快照：用来验证"校验不过就不许入队"。"""

    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "mathbank-full-backup"}))
    return path


# ---------------------------------------------------------------------------
# 快照清单
# ---------------------------------------------------------------------------


def test_listing_is_empty_and_last_backup_null_before_any_snapshot(client, backup_sandbox):
    response = client.get("/api/backups")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["snapshots"] == []
    assert data["last_backup_at"] is None
    assert data["pending_restore"] is None
    assert data["restore_ttl_seconds"] > 0
    assert data["dir"] == str(backup_sandbox["snapshots"])


def test_listing_reports_snapshot_facts_and_last_backup_time(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 4, "live")
    archive = _make_snapshot(backup_sandbox)

    data = client.get("/api/backups").json()

    assert [item["file"] for item in data["snapshots"]] == [archive.name]
    assert data["last_backup_at"] == data["snapshots"][0]["manifest"]["created_at"]
    assert data["snapshots"][0]["manifest"]["row_counts"]["questions"] == 4


def test_listing_flags_a_corrupt_snapshot_without_hiding_the_good_one(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 2, "live")
    good = _make_snapshot(backup_sandbox)
    _write_junk_archive(backup_sandbox["snapshots"], "mathbank-backup-20260101T000000000000Z.zip")

    data = client.get("/api/backups").json()

    by_file = {item["file"]: item for item in data["snapshots"]}
    assert by_file[good.name]["manifest"]["readable"] is True
    assert by_file["mathbank-backup-20260101T000000000000Z.zip"]["manifest"]["readable"] is False
    # 「上次备份时间」必须来自可读的那一份，不能被坏包带偏。
    assert data["last_backup_at"] == by_file[good.name]["manifest"]["created_at"]


# ---------------------------------------------------------------------------
# POST /api/backup/verify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_name",
    ["", "   ", "../math_question_bank.db", "/etc/passwd", "notes.txt"],
)
def test_verify_rejects_bad_names_with_400(client, backup_sandbox, file_name):
    response = client.post("/api/backup/verify", json={"file": file_name}, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_verify_rejects_well_formed_but_absent_name(client, backup_sandbox):
    response = client.post(
        "/api/backup/verify",
        json={"file": "mathbank-backup-20260915T133058872611Z.zip"},
        headers=AUTH,
    )

    assert response.status_code == 400
    assert "快照不存在" in response.json()["message"]


def test_verify_returns_422_for_corrupt_archive(client, backup_sandbox):
    broken = _write_junk_archive(
        backup_sandbox["snapshots"], "mathbank-backup-20260101T000000000000Z.zip"
    )

    response = client.post("/api/backup/verify", json={"file": broken.name}, headers=AUTH)

    assert response.status_code == 422
    assert "校验未通过" in response.json()["message"]


def test_verify_passes_on_a_real_snapshot(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 3, "live")
    archive = _make_snapshot(backup_sandbox)

    response = client.post("/api/backup/verify", json={"file": archive.name}, headers=AUTH)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["file"] == archive.name
    assert data["row_counts"]["questions"] == 3
    assert data["size_bytes"] == archive.stat().st_size


# ---------------------------------------------------------------------------
# POST /api/backup/restore —— 登记而不是立刻动数据
# ---------------------------------------------------------------------------


def test_restore_without_confirm_flag_is_refused(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 2, "live")
    archive = _make_snapshot(backup_sandbox)

    for body in ({"file": archive.name}, {"file": archive.name, "confirm": False},
                 {"file": archive.name, "confirm": "true"}):
        response = client.post("/api/backup/restore", json=body, headers=AUTH)
        assert response.status_code == 400
        assert "二次确认" in response.json()["message"]

    assert not backup_sandbox["pending"].exists()


def test_restore_refuses_archive_that_fails_verification(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 2, "live")
    broken = _write_junk_archive(
        backup_sandbox["snapshots"], "mathbank-backup-20260101T000000000000Z.zip"
    )

    response = client.post(
        "/api/backup/restore",
        json={"file": broken.name, "confirm": True},
        headers=AUTH,
    )

    assert response.status_code == 422
    # 坏快照不许入队，也不该白白产生一份安全备份。
    assert not backup_sandbox["pending"].exists()
    assert not backup_sandbox["pre_restore"].exists()


def test_restore_queues_request_and_leaves_current_data_untouched(client, backup_sandbox):
    # 先做一个「旧状态」的快照（2 题），再把库改成现在的样子（5 题）。
    _make_database(backup_sandbox["database"], 2, "old")
    archive = _make_snapshot(backup_sandbox)
    _make_database(backup_sandbox["database"], 5, "live")

    response = client.post(
        "/api/backup/restore",
        json={"file": archive.name, "confirm": True},
        headers=AUTH,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["file"] == archive.name
    assert "重新启动" in data["message"]

    # 1) 请求已落盘且格式正确
    pending = json.loads(backup_sandbox["pending"].read_text(encoding="utf-8"))
    assert pending["format"] == PENDING_RESTORE_FORMAT
    assert pending["archive"] == archive.name
    assert pending["safety_backup"]

    # 2) 当前库留下了安全备份——「还原也能再还原回去」
    safety = Path(pending["safety_backup"])
    assert safety.parent == backup_sandbox["pre_restore"].resolve()
    assert safety.is_file()

    # 3) 关键：现在这一刻数据一个字节都没动
    assert _question_count(backup_sandbox["database"]) == 5

    # 4) 界面能从列表接口看到这条待还原请求
    listing = client.get("/api/backups").json()
    assert listing["pending_restore"]["archive"] == archive.name


def test_queued_request_is_applied_on_next_start(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 2, "old")
    archive = _make_snapshot(backup_sandbox)
    _make_database(backup_sandbox["database"], 5, "live")
    client.post(
        "/api/backup/restore",
        json={"file": archive.name, "confirm": True},
        headers=AUTH,
    )

    # 模拟"用户关掉服务再启动"：启动钩子做的事就是这一句。
    result = apply_pending_restore()

    assert result["archive"] == archive.name
    assert _question_count(backup_sandbox["database"]) == 2
    assert not backup_sandbox["pending"].exists()


def test_cancel_removes_the_queued_request(client, backup_sandbox):
    _make_database(backup_sandbox["database"], 2, "live")
    archive = _make_snapshot(backup_sandbox)
    client.post(
        "/api/backup/restore",
        json={"file": archive.name, "confirm": True},
        headers=AUTH,
    )
    assert backup_sandbox["pending"].exists()

    first = client.delete("/api/backup/restore", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["removed"] is True
    assert not backup_sandbox["pending"].exists()

    second = client.delete("/api/backup/restore", headers=AUTH)
    assert second.json()["removed"] is False
    # 取消之后重启不该再动数据。
    assert apply_pending_restore() is None


# ---------------------------------------------------------------------------
# 令牌护栏
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/backup/verify", {"file": "mathbank-backup-20260915T133058872611Z.zip"}),
        ("post", "/api/backup/restore", {"file": "x", "confirm": True}),
        ("delete", "/api/backup/restore", None),
    ],
)
def test_write_endpoints_require_local_token(client, backup_sandbox, method, path, body):
    call = getattr(client, method)
    response = call(path, json=body) if body is not None else call(path)

    assert response.status_code == 403
    assert not backup_sandbox["pending"].exists()


def test_listing_is_readable_without_token(client, backup_sandbox):
    assert client.get("/api/backups").status_code == 200


# ---------------------------------------------------------------------------
# 启动钩子：必须卡在「持锁之后、开库之前」
# ---------------------------------------------------------------------------


def test_startup_applies_pending_restore_between_lock_and_database_init():
    main_source = (
        Path(__file__).resolve().parent.parent / "main.py"
    ).read_text(encoding="utf-8")

    lock_call = main_source.index(
        "_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()"
    )
    database_init = main_source.index("\ninit_db()", lock_call)
    window = main_source[lock_call:database_init]

    assert "apply_pending_restore()" in window
    # 还原换的是数据库文件本身，必须在任何一次开库/迁移之前发生。
    assert window.index("apply_pending_restore()") > window.index("atexit.register")
    # 测试环境不持锁、也不该动开发者本机的真实库。
    assert "_RUNTIME_LOCK is not None" in window


def test_restore_endpoint_keeps_the_delayed_contract():
    main_source = (
        Path(__file__).resolve().parent.parent / "main.py"
    ).read_text(encoding="utf-8")

    handler_start = main_source.index("def request_backup_restore(")
    handler_end = main_source.index("@app.delete(\"/api/backup/restore\")", handler_start)
    handler = main_source[handler_start:handler_end]

    assert "confirm" in handler
    # 在线阶段只允许登记；真正换库只能走 apply_pending_restore（启动窗口）。
    assert "restore_full_backup" not in handler
    assert "_restore_full_backup_unlocked" not in handler
    assert "write_pending_restore" in handler
