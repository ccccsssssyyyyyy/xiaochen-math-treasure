"""备份快照清单与「延迟还原」的端到端行为（合成库 + 临时目录，不碰真实数据）。

这一层锁死一条设计事实：服务持有 runtime lock 时不可能直接还原，所以 HTTP 层
只负责「登记请求」，真正的换库发生在下次启动的持锁窗口里。测试因此必须证明：

1. 快照清单只认规范命名的完整快照，读得懂 manifest 自述信息；
2. 文件名解析拒绝一切路径穿越；
3. 待还原请求写盘后能被读回，过期/损坏时自动作废而不是留在盘上等着咬人；
4. ``apply_pending_restore`` 真的会把库换成快照里的那一份，并且换完就清请求；
5. 换库失败时请求也被清掉，避免一个坏快照在每次重启时反复重放。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import mathbank.backup as backup_module
from mathbank.backup import (
    PENDING_RESTORE_FORMAT,
    apply_pending_restore,
    clear_pending_restore,
    create_full_backup,
    create_pre_restore_backup,
    list_full_backups,
    read_backup_manifest_summary,
    read_pending_restore,
    resolve_full_backup,
    verify_full_backup,
    write_pending_restore,
)

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


def _make_database(
    path: Path, question_count: int, marker: str, image_paths=None
) -> None:
    """造一个能被 verify_full_backup 认下的最小数据库（先清掉同名旧库）。"""

    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    referenced = json.dumps(image_paths or [])
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        for question_id in range(1, question_count + 1):
            connection.execute(
                "INSERT INTO questions (id, content, answer_markdown, image_paths)"
                " VALUES (?, ?, ?, ?)",
                (question_id, f"{marker}-question-{question_id}", None, referenced),
            )
            connection.execute(
                "INSERT INTO question_curriculums (id, question_id, version_code)"
                " VALUES (?, ?, ?)",
                (question_id, question_id, "A"),
            )
        connection.commit()


def _read_question_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0])


def _read_first_content(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        return str(connection.execute("SELECT content FROM questions ORDER BY id LIMIT 1").fetchone()[0])


def _seed_live_database(backup_sandbox, count: int, marker: str, image_paths=None) -> None:
    _make_database(backup_sandbox["database"], count, marker, image_paths)


def _make_snapshot(backup_sandbox) -> Path:
    """给当前库造一个规范命名的快照。"""

    return create_full_backup(
        output_dir=backup_sandbox["snapshots"],
        database_path=backup_sandbox["database"],
        uploads_dir=backup_sandbox["uploads"],
        metadata_path=backup_sandbox["data_backup"] / "custom_metadata.json",
        retention=None,
    )


# ---------------------------------------------------------------------------
# 1. 快照清单
# ---------------------------------------------------------------------------


def test_list_is_empty_and_last_backup_none_without_snapshots(backup_sandbox):
    assert list_full_backups() == []


def test_list_reports_manifest_facts_for_each_snapshot(backup_sandbox):
    _seed_live_database(backup_sandbox, 3, "live")
    archive = _make_snapshot(backup_sandbox)

    snapshots = list_full_backups()

    assert [item["file"] for item in snapshots] == [archive.name]
    entry = snapshots[0]
    assert entry["size_bytes"] == archive.stat().st_size
    assert entry["manifest"]["readable"] is True
    assert entry["manifest"]["row_counts"]["questions"] == 3
    assert entry["manifest"]["row_counts"]["question_curriculums"] == 3
    assert entry["manifest"]["created_at"].endswith("Z")
    assert "error" not in entry["manifest"]


def test_list_ignores_directories_and_foreign_names(backup_sandbox):
    (backup_sandbox["snapshots"] / "mathbank-backup-20260901T000000000000Z.zip").mkdir()
    (backup_sandbox["snapshots"] / "notes.txt").write_text("x", encoding="utf-8")
    (backup_sandbox["snapshots"] / "mathbank-backup-broken.zip").write_text("x", encoding="utf-8")

    assert list_full_backups() == []


def test_list_marks_unreadable_archive_instead_of_raising(backup_sandbox):
    broken = backup_sandbox["snapshots"] / "mathbank-backup-20260901T000000000000Z.zip"
    broken.write_text("not a zip", encoding="utf-8")

    snapshots = list_full_backups()

    assert len(snapshots) == 1
    assert snapshots[0]["manifest"]["readable"] is False
    assert snapshots[0]["manifest"]["error"]


def test_list_sorts_newest_first(backup_sandbox):
    _seed_live_database(backup_sandbox, 2, "old")
    older = _make_snapshot(backup_sandbox)
    _seed_live_database(backup_sandbox, 5, "new")
    newer = _make_snapshot(backup_sandbox)

    # 文件名决定 created_at，用 os.utime 无法伪造；这里按 manifest 里的时间断言。
    ordering = [item["file"] for item in list_full_backups()]
    assert older.name in ordering and newer.name in ordering
    created = [
        item["manifest"].get("created_at")
        for item in list_full_backups()
    ]
    assert created == sorted(created, reverse=True)


def test_read_manifest_summary_reports_error_for_non_archive(backup_sandbox):
    junk = backup_sandbox["tmp"] / "junk.zip"
    junk.write_bytes(b"nope")

    summary = read_backup_manifest_summary(junk)

    assert summary["readable"] is False
    assert "error" in summary


# ---------------------------------------------------------------------------
# 2. 文件名解析：路径穿越必须被挡住
# ---------------------------------------------------------------------------


def test_resolve_accepts_a_listed_snapshot(backup_sandbox):
    _seed_live_database(backup_sandbox, 1, "live")
    archive = _make_snapshot(backup_sandbox)

    assert resolve_full_backup(archive.name) == archive.resolve()


@pytest.mark.parametrize(
    "hostile",
    [
        "",
        "   ",
        "../pending_restore.json",
        "../../math_question_bank.db",
        "mathbank-backup-20260915T133058872611Z.zip/../../x",
        "/etc/passwd",
        "mathbank-backup-2026.09.15.zip",
        "mathbank-backup-20260915T133058872611Z.zip.bak",
        "mathbank-backup-20260915T133058872611Z.ZIP",
        "sub/mathbank-backup-20260915T133058872611Z.zip",
    ],
)
def test_resolve_rejects_hostile_names(backup_sandbox, hostile):
    with pytest.raises(RuntimeError):
        resolve_full_backup(hostile)


def test_resolve_rejects_well_formed_name_that_is_not_on_disk(backup_sandbox):
    with pytest.raises(RuntimeError, match="快照不存在"):
        resolve_full_backup("mathbank-backup-20260915T133058872611Z.zip")


# ---------------------------------------------------------------------------
# 3. 待还原请求的读写、过期与损坏
# ---------------------------------------------------------------------------


def test_pending_roundtrip_records_ttl_and_safety_backup(backup_sandbox):
    safety = backup_sandbox["pre_restore"] / "mathbank-backup-20260915T140000000000Z.zip"
    payload = write_pending_restore(
        "mathbank-backup-20260915T133058872611Z.zip",
        safety_backup=safety,
    )

    assert payload["format"] == PENDING_RESTORE_FORMAT
    assert payload["expires_at"] > payload["requested_at"]

    request_file = backup_sandbox["pending"]
    assert request_file.is_file()
    assert (request_file.stat().st_mode & 0o777) == 0o600

    restored = read_pending_restore()
    assert restored["archive"] == payload["archive"]
    # 必须留下可追的绝对路径：只记文件名，用户没法在 pre_restore/ 里找到退路。
    assert restored["safety_backup"] == str(safety)
    assert Path(restored["safety_backup"]).is_absolute()


def test_write_pending_restore_rejects_hostile_archive_name(backup_sandbox):
    with pytest.raises(RuntimeError):
        write_pending_restore("../../math_question_bank.db")
    assert not backup_sandbox["pending"].exists()


def test_expired_request_is_dropped_from_disk(backup_sandbox):
    write_pending_restore(
        "mathbank-backup-20260915T133058872611Z.zip", ttl_seconds=60
    )

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=61)
    assert read_pending_restore(now=later) is None
    assert not backup_sandbox["pending"].exists()


def test_request_still_valid_one_second_before_expiry(backup_sandbox):
    write_pending_restore(
        "mathbank-backup-20260915T133058872611Z.zip", ttl_seconds=60
    )

    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=59)
    assert read_pending_restore(now=soon) is not None


def test_corrupt_request_file_is_discarded_not_retried(backup_sandbox):
    backup_sandbox["pending"].write_text("{ not json", encoding="utf-8")

    assert read_pending_restore() is None
    assert not backup_sandbox["pending"].exists()


def test_foreign_format_request_is_discarded(backup_sandbox):
    backup_sandbox["pending"].write_text(
        json.dumps({"format": "something-else", "archive": "x"}), encoding="utf-8"
    )

    assert read_pending_restore() is None
    assert not backup_sandbox["pending"].exists()


def test_request_with_hostile_archive_is_discarded(backup_sandbox):
    backup_sandbox["pending"].write_text(
        json.dumps(
            {
                "format": PENDING_RESTORE_FORMAT,
                "archive": "../../../etc/passwd",
                "expires_at": (
                    dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )

    assert read_pending_restore() is None
    assert not backup_sandbox["pending"].exists()


def test_clear_reports_whether_something_was_removed(backup_sandbox):
    write_pending_restore("mathbank-backup-20260915T133058872611Z.zip")
    assert clear_pending_restore(reason="test") is True
    assert clear_pending_restore(reason="test") is False


def test_read_without_request_file_returns_none(backup_sandbox):
    assert read_pending_restore() is None


# ---------------------------------------------------------------------------
# 4. 落盘还原
# ---------------------------------------------------------------------------


def test_apply_pending_restore_swaps_database_and_clears_request(backup_sandbox):
    # 先做一个「两周前」的快照，再把库改成另一副样子。
    _seed_live_database(backup_sandbox, 3, "snapshot")
    archive = _make_snapshot(backup_sandbox)
    _seed_live_database(backup_sandbox, 9, "today")
    assert _read_question_count(backup_sandbox["database"]) == 9

    write_pending_restore(archive.name, safety_backup="mathbank-backup-x.zip")
    result = apply_pending_restore()

    assert result is not None
    assert result["archive"] == archive.name
    assert _read_question_count(backup_sandbox["database"]) == 3
    assert _read_first_content(backup_sandbox["database"]) == "snapshot-question-1"
    assert not backup_sandbox["pending"].exists()
    # 还原前的当前库必须留下了安全备份（这才是"还原也能再还原回去"的前提）。
    assert Path(result["safety_backup"]).is_file()


def test_apply_pending_restore_creates_pre_restore_safety_copy(backup_sandbox):
    _seed_live_database(backup_sandbox, 4, "snapshot")
    archive = _make_snapshot(backup_sandbox)
    _seed_live_database(backup_sandbox, 7, "today")
    write_pending_restore(archive.name)

    result = apply_pending_restore()

    safety = Path(result["safety_backup"])
    assert safety.parent == backup_sandbox["pre_restore"].resolve()
    # 安全备份里存的是「还原那一刻」的 9→7 题版本，不是被还原成的 4 题版本。
    assert verify_full_backup(safety)["database"]["row_counts"]["questions"] == 7


def test_apply_pending_restore_is_a_noop_without_request(backup_sandbox):
    _seed_live_database(backup_sandbox, 2, "live")

    assert apply_pending_restore() is None
    assert _read_question_count(backup_sandbox["database"]) == 2


def test_apply_pending_restore_ignores_expired_request(backup_sandbox):
    _seed_live_database(backup_sandbox, 2, "snapshot")
    archive = _make_snapshot(backup_sandbox)
    _seed_live_database(backup_sandbox, 8, "today")
    write_pending_restore(archive.name, ttl_seconds=60)

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=120)
    assert apply_pending_restore(now=later) is None
    assert _read_question_count(backup_sandbox["database"]) == 8


def test_apply_pending_restore_clears_request_when_archive_is_missing(backup_sandbox):
    _seed_live_database(backup_sandbox, 2, "live")
    write_pending_restore("mathbank-backup-20260915T133058872611Z.zip")

    with pytest.raises(RuntimeError):
        apply_pending_restore()

    # 快照不在盘上时请求已被读过一次；无论哪一步失败都不能留下反复重放的请求。
    assert not backup_sandbox["pending"].exists()
    assert _read_question_count(backup_sandbox["database"]) == 2


def test_apply_pending_restore_drops_request_when_archive_fails_verification(backup_sandbox):
    _seed_live_database(backup_sandbox, 2, "live")
    broken = backup_sandbox["snapshots"] / "mathbank-backup-20260915T133058872611Z.zip"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "mathbank-full-backup"}))
    write_pending_restore(broken.name)

    with pytest.raises(Exception):
        apply_pending_restore()

    assert not backup_sandbox["pending"].exists()
    assert _read_question_count(backup_sandbox["database"]) == 2


def test_apply_pending_restore_restores_uploads_alongside_database(backup_sandbox):
    # 只有被数据库引用的插图才会进备份（其余文件不在归档里，还原后本就该消失），
    # 所以必须让题目真的引用这张图，否则测的不是"图片跟着回滚"。
    _seed_live_database(
        backup_sandbox, 1, "snapshot", image_paths=["/static/uploads/shot.png"]
    )
    (backup_sandbox["uploads"] / "shot.png").write_bytes(b"\x89PNG-old")
    archive = _make_snapshot(backup_sandbox)

    # 还原前把上传目录换掉：还原后必须回到快照里的那一版。
    (backup_sandbox["uploads"] / "shot.png").write_bytes(b"\x89PNG-new")
    (backup_sandbox["uploads"] / "extra.png").write_bytes(b"\x89PNG-extra")

    write_pending_restore(archive.name)
    apply_pending_restore()

    assert (backup_sandbox["uploads"] / "shot.png").read_bytes() == b"\x89PNG-old"
    assert not (backup_sandbox["uploads"] / "extra.png").exists()


# ---------------------------------------------------------------------------
# 5. 还原前备份的落点
# ---------------------------------------------------------------------------


def test_create_pre_restore_backup_lands_outside_the_snapshot_list(backup_sandbox):
    _seed_live_database(backup_sandbox, 3, "live")

    safety = create_pre_restore_backup()

    assert safety.parent == backup_sandbox["pre_restore"].resolve()
    # 关键：它不能出现在用户的快照列表里，否则一次还原请求会顺手改写"上次备份时间"。
    assert list_full_backups() == []
    assert verify_full_backup(safety)["database"]["row_counts"]["questions"] == 3
