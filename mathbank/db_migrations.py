"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR


LATEST_SCHEMA_VERSION = 1004
REQUIRED_TABLES = {"questions", "question_curriculums", "papers", "paper_questions"}


def _database_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_version(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def create_pre_migration_backup(
    engine: Engine,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    """Create and verify a consistent SQLite snapshot before a migration."""

    database_path = _database_path(engine)
    if database_path is None or not database_path.exists():
        return None

    backup_dir = SCHEMA_SNAPSHOT_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"{database_path.stem}.schema-v{from_version}-to-v{to_version}.{timestamp}.db"
    )

    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
        journal_mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise RuntimeError(
                f"迁移前快照无法转换为独立日志模式: {journal_mode}"
            )
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"迁移前数据库快照校验失败: {integrity}")

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{backup_path}{suffix}")
        if suffix == "-wal" and sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(f"迁移前快照仍依赖未归档 WAL: {sidecar}")
        sidecar.unlink(missing_ok=True)

    backup_path.chmod(0o600)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(backup_path)}  {backup_path.name}\n", encoding="utf-8")
    checksum_path.chmod(0o600)
    return backup_path


def _rebuild_relationship_tables(engine: Engine) -> dict[str, int]:
    """Rebuild relation tables with real FKs while repairing legacy drift."""

    stats: dict[str, int] = {}
    # AUTOCOMMIT lets us issue an explicit BEGIN IMMEDIATE.  Relying on the
    # sqlite3 driver's implicit transaction behavior is unsafe for DDL because
    # older driver modes may otherwise auto-commit CREATE/DROP statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True

            # v3: content_fingerprint 列 + 存量回填（查重性能优化 1+2）。
            # 与关系表重建在同一事务内完成，原子提交或回滚。
            _cols = [
                r[1]
                for r in connection.exec_driver_sql(
                    "PRAGMA table_info(questions)"
                ).fetchall()
            ]
            if "content_fingerprint" not in _cols:
                connection.exec_driver_sql(
                    "ALTER TABLE questions ADD COLUMN content_fingerprint VARCHAR"
                )
            _rows = connection.exec_driver_sql(
                "SELECT id, content FROM questions "
                "WHERE content_fingerprint IS NULL OR content_fingerprint = ''"
            ).fetchall()
            if _rows:
                from mathbank.database import normalize_question_content
                for _qid, _qcontent in _rows:
                    _fp = normalize_question_content(_qcontent or "")
                    connection.exec_driver_sql(
                        "UPDATE questions SET content_fingerprint = ? WHERE id = ?",
                        (_fp, _qid),
                    )

            before_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            before_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS question_curriculums__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE question_curriculums__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT '',
                    CONSTRAINT uq_question_curriculum_version
                        UNIQUE (question_id, version_code),
                    CONSTRAINT fk_question_curriculums_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO question_curriculums__new
                    (id, question_id, version_code, compulsory, chapter, knowledge)
                SELECT qc.id, qc.question_id, qc.version_code,
                       qc.compulsory, qc.chapter, qc.knowledge
                FROM question_curriculums AS qc
                JOIN questions AS q ON q.id = qc.question_id
                JOIN (
                    SELECT question_id, version_code, MAX(id) AS keep_id
                    FROM question_curriculums
                    GROUP BY question_id, version_code
                ) AS newest ON newest.keep_id = qc.id
                """
            )
            connection.exec_driver_sql("DROP TABLE question_curriculums")
            connection.exec_driver_sql(
                "ALTER TABLE question_curriculums__new RENAME TO question_curriculums"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_lookup "
                "ON question_curriculums (version_code, compulsory, chapter, knowledge)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_qid "
                "ON question_curriculums (question_id)"
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS paper_questions__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE paper_questions__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    paper_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 5 CHECK (score >= 0),
                    CONSTRAINT uq_paper_question_order UNIQUE (paper_id, order_index),
                    CONSTRAINT fk_paper_questions_paper
                        FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_paper_questions_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO paper_questions__new
                    (id, paper_id, question_id, order_index, score)
                SELECT pq.id, pq.paper_id, pq.question_id,
                       COALESCE(pq.order_index, 0),
                       CASE WHEN pq.score IS NULL OR pq.score < 0 THEN 0 ELSE pq.score END
                FROM paper_questions AS pq
                JOIN papers AS p ON p.id = pq.paper_id
                JOIN questions AS q ON q.id = pq.question_id
                JOIN (
                    SELECT paper_id, COALESCE(order_index, 0), MAX(id) AS keep_id
                    FROM paper_questions
                    GROUP BY paper_id, COALESCE(order_index, 0)
                ) AS newest ON newest.keep_id = pq.id
                """
            )
            connection.exec_driver_sql("DROP TABLE paper_questions")
            connection.exec_driver_sql(
                "ALTER TABLE paper_questions__new RENAME TO paper_questions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_paper_id ON paper_questions (paper_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_question_id ON paper_questions (question_id)"
            )
            connection.exec_driver_sql(
                """
                UPDATE papers
                SET total_score = COALESCE(
                    (
                        SELECT SUM(pq.score)
                        FROM paper_questions AS pq
                        WHERE pq.paper_id = papers.id
                    ),
                    0
                )
                """
            )

            remaining_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            remaining_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"迁移后仍存在外键异常: {violations[:5]}")

            # v3 步骤只负责把结构升级到 v3（含 content_fingerprint 列 + 首次回填）。
            # 注意：此处固定落到 3，不再跟随 LATEST，否则会跳过后续的 v4 步骤。
            connection.exec_driver_sql("PRAGMA user_version=3")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            stats = {
                "removed_question_curriculums": before_curriculums - remaining_curriculums,
                "removed_paper_questions": before_paper_questions - remaining_paper_questions,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
            if enabled != 1:
                raise RuntimeError("迁移连接未能恢复 SQLite 外键检查")
    return stats


def _recompute_fingerprints_v4(engine: Engine) -> dict[str, int]:
    """v4: 用修正后的 normalize_question_content 重算全部 content_fingerprint。

    修正点：归一化时剥离图片 markdown（![...](...)），避免 OCR 生成的随机
    uuid/hash 污染指纹，导致带图题目二次入库漏报重复。存量数据必须整表重算，
    否则旧行仍带图片 uuid，与同题的新行对不齐、依旧漏报。

    本步骤只更新 questions.content_fingerprint，不触碰结构与其他表，因此无需
    关闭外键检查；仍沿用 AUTOCOMMIT + 显式 BEGIN IMMEDIATE 的事务边界以保证原子性。
    """

    from mathbank.database import normalize_question_content

    stats: dict[str, int] = {"recomputed_fingerprints": 0}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            rows = connection.exec_driver_sql(
                "SELECT id, content FROM questions"
            ).fetchall()
            for _qid, _qcontent in rows:
                _fp = normalize_question_content(_qcontent or "")
                connection.exec_driver_sql(
                    "UPDATE questions SET content_fingerprint = ? WHERE id = ?",
                    (_fp, _qid),
                )
            stats["recomputed_fingerprints"] = len(rows)
            connection.exec_driver_sql("PRAGMA user_version=4")
            connection.exec_driver_sql("COMMIT")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
    return stats


def _bump_to_fork_v1004(engine: Engine) -> dict[str, int]:
    """本 fork 版本线基线跳号：v4 -> v1004（仅写 user_version，不动数据）。

    本派生 fork 自 JudgePeach/math-question-bank，后者已演进到 v8+。为避免本 fork
    的数据库与上游数据库互换时 ``RuntimeError: 数据库版本高于程序支持版本`` 误伤用户，
    把 fork 的版本线偏移到 ``1000 + 上游版本号``（v4 -> 1004）。该步骤：

    - 不重建表、不重算指纹、不动数据；
    - 仅写入 ``PRAGMA user_version = 1004``；
    - 对尚未经历过上游 v5+ 迁移的 fork 用户库是「无操作」式的版本号跳号，幂等可重复执行。
    """
    stats: dict[str, int] = {"bumped_to_v1004": 1}
    with engine.begin() as connection:
        try:
            connection.exec_driver_sql("PRAGMA user_version=1004")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
    return stats


def migrate_database(
    engine: Engine,
    *,
    pre_migration_backup: Path | None = None,
) -> dict[str, object]:
    """Upgrade the connected SQLite database and fail loudly on errors."""

    current = schema_version(engine)
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于程序支持版本 {LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    if current == LATEST_SCHEMA_VERSION:
        return {"from_version": current, "to_version": current, "backup": None}

    table_names = set(inspect(engine).get_table_names())
    if not table_names:
        # A caller may version an empty database immediately before creating the
        # current ORM metadata.
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        return {"from_version": current, "to_version": LATEST_SCHEMA_VERSION, "backup": None}
    if not REQUIRED_TABLES.issubset(table_names):
        missing = ", ".join(sorted(REQUIRED_TABLES - table_names))
        raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

    original_version = current
    backup = pre_migration_backup or create_pre_migration_backup(
        engine, from_version=current, to_version=LATEST_SCHEMA_VERSION
    )
    # 按版本号逐步升级：每个步骤只负责把结构推进到自己的目标版本，
    # 避免一次性大事务把 v3 关系重建与 v4 指纹重算耦合，也便于失败回滚定位。
    stats: dict[str, object] = {}
    while current < LATEST_SCHEMA_VERSION:
        if current < 3:
            # v3：关系表重建 + content_fingerprint 列与首次回填（落 user_version=3）
            step_stats = _rebuild_relationship_tables(engine)
            current = 3
        elif current == 3:
            # v4：用修正后的归一化规则整表重算指纹（落 user_version=3 → 4）
            step_stats = _recompute_fingerprints_v4(engine)
            current = 4
        elif current == 4:
            # v1004：本 fork 版本线基线跳号（不重建数据，仅写 user_version=1004）。
            # 见 _bump_to_fork_v1004 函数文档。
            step_stats = _bump_to_fork_v1004(engine)
            current = 1004
        else:
            raise RuntimeError(
                f"未实现从版本 {current} 到 {LATEST_SCHEMA_VERSION} 的迁移，"
                f"请升级 math-question-bank 后再打开此数据库。"
            )
        if step_stats:
            stats.update(step_stats)
    return {
        "from_version": original_version,
        "to_version": LATEST_SCHEMA_VERSION,
        "backup": str(backup) if backup else None,
        **stats,
    }
