"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR


LATEST_SCHEMA_VERSION = 1012
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


def _add_mistake_tables_v1005(engine: Engine) -> dict[str, int]:
    """v1005：新增错题扫描三表（students / mistake_batches / mistake_records）。

    本步骤**只建新表与新索引**，不改动 ``questions`` / ``papers`` /
    ``question_curriculums`` / ``paper_questions`` 中的任何一列或任何一行数据 ——
    这是「物化先不进题库」这一决策在数据层的直接体现。

    建表走 ORM 的 ``Base.metadata.create_all``（``checkfirst=True``）而非手写
    DDL，保证迁移建出的结构与 ``mathbank.database`` 的模型定义永不漂移；
    本步骤因此天然幂等，可被重复执行。
    """

    from mathbank.database import Base  # 延迟导入：database 模块反向依赖本模块

    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    created = sorted(set(inspect(engine).get_table_names()) - before)

    with engine.begin() as connection:
        # 组合索引（单列索引已由模型上的 index=True 带出）
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_mistake_records_student_subject "
            "ON mistake_records (student_id, subject)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_mistake_batches_student "
            "ON mistake_batches (student_id, batch_date)"
        )
        connection.exec_driver_sql("PRAGMA user_version=1005")

    return {"created_mistake_tables": len(created)}


def _add_mistake_block_columns_v1006(engine: Engine) -> dict[str, int]:
    """v1006：``mistake_records`` 增加题块的横向范围与栏号。

    v1005 建的 ``mistake_records`` 只有纵向范围（``block_y_start`` /
    ``block_y_end``）—— 在单栏页面上够用，但双栏卷子上无法表达「这一块属于哪
    一栏」。少了横向信息，裁块图会把左栏半道题和右栏半道题拼进同一张图。

    本步骤只加三列：``block_x_start`` / ``block_x_end`` / ``column_index``。
    旧记录**不回填**：默认值（x 0–1、column 0）恰好就是「整页一栏」的语义，
    与它们当时在单栏算法下切出来的事实一致。

    可重复执行：列已存在时跳过。
    """

    inspector = inspect(engine)
    if "mistake_records" not in set(inspector.get_table_names()):
        # 空库 / 尚未建表：无表可改，直接推进版本号；建表由
        # _add_mistake_tables_v1005 与 Base.metadata.create_all 负责。
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=1006")
        return {"added_mistake_block_columns": 0}

    existing = {column["name"] for column in inspector.get_columns("mistake_records")}
    additions = {
        "block_x_start": "FLOAT DEFAULT 0.0",
        "block_x_end": "FLOAT DEFAULT 1.0",
        "column_index": "INTEGER DEFAULT 0",
    }
    added = 0
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE mistake_records ADD COLUMN {name} {ddl}"
            )
            added += 1
        connection.exec_driver_sql("PRAGMA user_version=1006")

    return {"added_mistake_block_columns": added}


def _add_mistake_merge_columns_v1007(engine: Engine) -> dict[str, int]:
    """v1007：``mistake_records`` 增加「人工合并」三列。

    一道题被分栏或排版切成多块时，用户会把它们并为一条记录。合并后的记录仍是
    一条（``image_block`` 是合成后的一张图），但需要三个字段记住它来自哪几块：

    - ``merge_id``：合并组标识（空＝未合并），拆分时据此定位；
    - ``merged_block_count``：成员块数（1 ＝未合并）；
    - ``block_images``：按拼接顺序存各成员块图 URL 的 JSON 数组。

    旧记录**不回填**：三列的默认值（空 / 1 / 空数组）恰好就是「未合并」的语义。

    可重复执行：列已存在时跳过。
    """

    inspector = inspect(engine)
    if "mistake_records" not in set(inspector.get_table_names()):
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=1007")
        return {"added_mistake_merge_columns": 0}

    existing = {column["name"] for column in inspector.get_columns("mistake_records")}
    additions = {
        "merge_id": "VARCHAR(50) DEFAULT ''",
        "merged_block_count": "INTEGER DEFAULT 1",
        "block_images": "TEXT DEFAULT '[]'",
    }
    added = 0
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE mistake_records ADD COLUMN {name} {ddl}"
            )
            added += 1
        connection.exec_driver_sql("PRAGMA user_version=1007")

    return {"added_mistake_merge_columns": added}


def _add_question_subject_origin_v1008(engine: Engine) -> dict[str, int]:
    """v1008：``questions`` 增加 ``subject`` / ``origin`` 两列（三科题库）。

    背景：一期「物化错题不进题库」的约束作废 —— 物理（教科版）与化学（人教版）
    错题也要入库；同时需要把「错题入库」的题与「题库工作台自录/导入」的题区分开。

    - ``subject``：math / physics / chemistry。存量行全是数学，回填 ``math``；
    - ``origin``：bank（题库工作台录入、导入试卷）/ mistake（错题工作台入库）。
      存量行回填 ``bank`` —— 这是准确的：此前的错题从来没进过题库。

    ``ALTER TABLE ... ADD COLUMN ... DEFAULT`` 由 SQLite 自动用默认值回填存量行，
    因此 UPDATE 只是兜住「列已存在但值为空」的中间态（例如手工执行过 DDL）。
    可重复执行：列已存在时跳过新增。
    """

    inspector = inspect(engine)
    if "questions" not in set(inspector.get_table_names()):
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=1008")
        return {"added_question_subject_origin": 0}

    existing = {column["name"] for column in inspector.get_columns("questions")}
    additions = {
        "subject": "VARCHAR(50) DEFAULT 'math'",
        "origin": "VARCHAR(20) DEFAULT 'bank'",
    }
    added = 0
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {name} {ddl}"
            )
            added += 1
        connection.exec_driver_sql(
            "UPDATE questions SET subject = 'math' "
            "WHERE subject IS NULL OR subject = ''"
        )
        connection.exec_driver_sql(
            "UPDATE questions SET origin = 'bank' "
            "WHERE origin IS NULL OR origin = ''"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions (subject)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_questions_origin ON questions (origin)"
        )
        connection.exec_driver_sql("PRAGMA user_version=1008")

    return {"added_question_subject_origin": added}


def _strip_block_url_version_v1009(engine: Engine) -> dict[str, int]:
    """v1009：把误写进库的块图 URL 版本号（``?v=<mtime_ns>``）剥掉。

    背景：v2.3.x 早期为了修「同名覆盖 → 浏览器复用旧位图」，把带 ``?v=`` 的 URL
    直接写进了 ``mistake_records.image_block`` / ``block_images``。但资产安全层
    （``asset_security._reference_parts``）拒绝任何含 ``?`` 的引用，于是识别取图
    100% 失败、整批标 ``failed``、题面全空（排查见
    .workbuddy/memory/2026-09-15.md 第六轮）。

    写入端已改为只存裸路径，但**已经切好的批次**库里仍然是脏的 —— 不洗就永远
    识别不了，所以这一步是必需的，不是可选的清理。

    幂等：不含 ``?`` 的行原样保留、也不会被 UPDATE。
    """

    if "mistake_records" not in set(inspect(engine).get_table_names()):
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=1009")
        return {"stripped_block_url_version": 0}

    def _strip(value: object) -> str:
        text = str(value or "")
        if "?" not in text:
            return text
        return text.split("?", 1)[0].split("#", 1)[0]

    image_rows = 0
    member_rows = 0
    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, image_block, block_images FROM mistake_records"
        ).fetchall()
        for row in rows:
            record_id, raw_image, raw_images = row[0], row[1] or "", row[2] or ""
            new_image = _strip(raw_image)
            new_images = str(raw_images)
            if raw_images:
                try:
                    members = json.loads(raw_images)
                except (TypeError, ValueError):
                    members = None
                if isinstance(members, list):
                    cleaned = [_strip(item) for item in members]
                    if cleaned != members:
                        new_images = json.dumps(cleaned, ensure_ascii=False)
            changed_image = new_image != raw_image
            changed_members = new_images != str(raw_images)
            if changed_image:
                image_rows += 1
            if changed_members:
                member_rows += 1
            if changed_image or changed_members:
                connection.exec_driver_sql(
                    "UPDATE mistake_records SET image_block = ?, block_images = ? "
                    "WHERE id = ?",
                    (new_image, new_images, record_id),
                )
        connection.exec_driver_sql("PRAGMA user_version=1009")

    return {
        "stripped_block_url_version": image_rows,
        "stripped_block_url_members": member_rows,
    }


def _add_mistake_classification_columns_v1010(engine: Engine) -> dict[str, int]:
    """v1010：``mistake_records`` 增加「分类信息」六列（审校页可编辑，入库带进题库）。

    用户 2026-09-15 定：错题本审校页中栏做成与题库录入表单同款的「分类信息」，
    字段口径与 ``questions`` 表逐字对齐：

    - ``source``：来源（试卷/出处），与 ``snapshot_source``（批次文件名快照）各记各的；
    - ``category_compulsory`` / ``category_chapter`` / ``category_knowledge``：学段/章节/小节；
    - ``related_curriculums``：关联章节 JSON 数组（融合题多章节归属）；
    - ``tags``：自定义标签。

    痛点是入库：迁移前 ``_import_mistake_record_to_bank`` 把学段/章节/小节写死成空字符串，
    错题进题库后在「章节」筛选里根本找不到。加列只是给这些值一个落脚点，**不改动
    任何既有行的语义** —— 默认值（空串 / ``[]``）恰好等于「未分类」。

    可重复执行：列已存在时跳过。
    """

    inspector = inspect(engine)
    if "mistake_records" not in set(inspector.get_table_names()):
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=1010")
        return {"added_mistake_classification_columns": 0}

    existing = {column["name"] for column in inspector.get_columns("mistake_records")}
    additions = {
        "source": "VARCHAR(200) DEFAULT ''",
        "category_compulsory": "VARCHAR(100) DEFAULT ''",
        "category_chapter": "VARCHAR(100) DEFAULT ''",
        "category_knowledge": "VARCHAR(100) DEFAULT ''",
        "related_curriculums": "TEXT DEFAULT '[]'",
        "tags": "TEXT DEFAULT ''",
    }
    added = 0
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE mistake_records ADD COLUMN {name} {ddl}"
            )
            added += 1
        connection.exec_driver_sql("PRAGMA user_version=1010")

    return {"added_mistake_classification_columns": added}


def _add_redo_tables_and_question_columns_v1011(engine: Engine) -> dict[str, int]:
    """v1011：错题重做闭环 —— 新增 ``redo_attempts`` 表 + ``questions`` 七列。

    背景：错题工作台此前只做到「错题进题库 / 出这次错题本」，没有「重做 → 录入
    二遍结果 → 跟踪掌握度」的闭环。本步骤给数据层补上落脚点：

    - 新表 ``redo_attempts``：每次重做、每道题一行（掌握度计算的事实来源）；
    - ``questions`` 新增七列：``correct_answer``（选项打乱后重映射答案键的依据）、
      ``wrong_count`` / ``redo_count`` / ``redo_correct_streak`` /
      ``mastery_status`` / ``next_redo_due`` / ``last_redo_at``。

    建表走 ORM 的 ``Base.metadata.create_all``（``checkfirst=True``），与 v1005
    一致，保证迁移建出的结构与 ``mathbank.database`` 的模型定义永不漂移；加列走
    ``ALTER TABLE``，**只加列、不回填** —— 默认值（0 / '' / NULL）恰好等于
    「从未重做过」的语义。幂等可重跑。
    """

    from mathbank.database import Base  # 延迟导入：database 模块反向依赖本模块

    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    created = sorted(set(inspect(engine).get_table_names()) - before)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    added = 0
    additions = {
        "correct_answer": "VARCHAR(10) DEFAULT ''",
        "wrong_count": "INTEGER DEFAULT 0",
        "redo_count": "INTEGER DEFAULT 0",
        "redo_correct_streak": "INTEGER DEFAULT 0",
        "mastery_status": "VARCHAR(20) DEFAULT 'pending'",
        "next_redo_due": "DATE",
        "last_redo_at": "DATETIME",
    }
    with engine.begin() as connection:
        if "questions" in table_names:
            existing = {column["name"] for column in inspector.get_columns("questions")}
            for name, ddl in additions.items():
                if name in existing:
                    continue
                connection.exec_driver_sql(
                    f"ALTER TABLE questions ADD COLUMN {name} {ddl}"
                )
                added += 1
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_questions_mastery_status "
                "ON questions (mastery_status)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_questions_next_redo_due "
                "ON questions (next_redo_due)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_questions_wrong_count "
                "ON questions (wrong_count)"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_redo_attempts_question "
            "ON redo_attempts (question_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_redo_attempts_paper "
            "ON redo_attempts (paper_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_redo_attempts_student_result "
            "ON redo_attempts (student_id, result)"
        )
        connection.exec_driver_sql("PRAGMA user_version=1011")

    return {
        "created_redo_tables": len(created),
        "added_question_redo_columns": added,
    }


def _add_mastery_override_columns_v1012(engine: Engine) -> dict[str, int]:
    """v1012：``questions`` 补「人工标注掌握度」两列。

    背景：掌握度由 ``redo_attempts`` 重放推导（跨日连对 2 次 → 已掌握），但老师
    有时需要自己说了算 —— 比如「这道题她口头讲对了」「这题其实不难，不用再排」。
    于是加一个人工覆盖通道。

    - ``mastery_override``：``'mastered'`` 或空串（空 = 无人工标注）；
    - ``mastery_override_at``：标注时刻，**决定它在重放序列里的位置**。

    为什么不用「往 ``redo_attempts`` 插一行」来实现：``redo_count`` 是
    ``len(attempts)``，而 ``_redo_attempt_no = redo_count + 1`` 决定导出时的选项
    变换档位（第 3 遍起剥选项）。插一行会把每道被标注的题往后推一遍，第 3 遍的
    「无选项」档直接错位成第 4 遍 —— 而这是印在纸面上的，学生拿着卷子就发现不对。

    语义是**事件归并**：人工标注当作历史里的一条按时间戳插入的事件，与
    ``redo_attempts`` 一起排序重放。因此之后更晚的录入能重新接管状态（又做错了
    就回到未掌握），而补录一张旧卷（录入日期早于标注时刻）不会误推翻标注。

    只加列、不回填：空串 / NULL 恰好等于「从未人工标注过」。幂等可重跑。
    """

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    added = 0
    additions = {
        "mastery_override": "VARCHAR(20) DEFAULT ''",
        "mastery_override_at": "DATETIME",
    }
    with engine.begin() as connection:
        if "questions" in table_names:
            existing = {column["name"] for column in inspector.get_columns("questions")}
            for name, ddl in additions.items():
                if name in existing:
                    continue
                connection.exec_driver_sql(
                    f"ALTER TABLE questions ADD COLUMN {name} {ddl}"
                )
                added += 1
        connection.exec_driver_sql("PRAGMA user_version=1012")

    return {"added_question_mastery_override_columns": added}


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
        elif current == 1004:
            # v1005：错题扫描三表（students / mistake_batches / mistake_records）。
            # 只建新表，不动既有表 —— 见 _add_mistake_tables_v1005 函数文档。
            step_stats = _add_mistake_tables_v1005(engine)
            current = 1005
        elif current == 1005:
            # v1006：mistake_records 补题块横向范围与栏号（双栏支持）。
            # 只加列、不回填 —— 见 _add_mistake_block_columns_v1006 函数文档。
            step_stats = _add_mistake_block_columns_v1006(engine)
            current = 1006
        elif current == 1006:
            # v1007：mistake_records 补人工合并三列（merge_id / merged_block_count
            # / block_images）—— 见 _add_mistake_merge_columns_v1007 函数文档。
            step_stats = _add_mistake_merge_columns_v1007(engine)
            current = 1007
        elif current == 1007:
            # v1008：questions 补 subject / origin 两列（三科题库 + 错题渠道区分）
            # —— 见 _add_question_subject_origin_v1008 函数文档。
            step_stats = _add_question_subject_origin_v1008(engine)
            current = 1008
        elif current == 1008:
            # v1009：清洗误入库的块图 URL 版本号（?v=<mtime_ns>），
            # 否则已切好的批次永远识别不了 —— 见该函数文档。
            step_stats = _strip_block_url_version_v1009(engine)
            current = 1009
        elif current == 1009:
            # v1010：mistake_records 补「分类信息」六列（审校页可编辑、入库带进题库）。
            # 只加列、不回填 —— 见 _add_mistake_classification_columns_v1010 函数文档。
            step_stats = _add_mistake_classification_columns_v1010(engine)
            current = 1010
        elif current == 1010:
            # v1011：错题重做闭环 —— 新增 redo_attempts 表 + questions 七列
            # （correct_answer / wrong_count / redo_count / redo_correct_streak /
            # mastery_status / next_redo_due / last_redo_at）。只建表加列、不回填
            # —— 见 _add_redo_tables_and_question_columns_v1011 函数文档。
            step_stats = _add_redo_tables_and_question_columns_v1011(engine)
            current = 1011
        elif current == 1011:
            # v1012：questions 补「人工标注掌握度」两列（mastery_override /
            # mastery_override_at）。掌握度仍由重放推导，人工标注只是按时间戳插进
            # 历史的一条事件 —— 见 _add_mastery_override_columns_v1012 函数文档。
            step_stats = _add_mastery_override_columns_v1012(engine)
            current = 1012
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
