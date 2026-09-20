import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

from mathbank import database as database_module
from mathbank import db_migrations


def _create_legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                paper_type TEXT DEFAULT 'exam',
                total_score INTEGER DEFAULT 150,
                metadata_json TEXT DEFAULT '{}',
                created_at DATETIME
            );
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL,
                version_code VARCHAR(50) NOT NULL,
                compulsory VARCHAR(100) DEFAULT '',
                chapter VARCHAR(100) DEFAULT '',
                knowledge VARCHAR(100) DEFAULT ''
            );
            CREATE TABLE paper_questions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                order_index INTEGER DEFAULT 0,
                score INTEGER DEFAULT 5
            );
            INSERT INTO questions VALUES (1, 'question');
            INSERT INTO papers VALUES (1, 'paper', '', 'exam', 999, '{}', NULL);
            INSERT INTO question_curriculums VALUES (1, 1, 'A', 'old', '', '');
            INSERT INTO question_curriculums VALUES (2, 1, 'A', 'new', '', '');
            INSERT INTO question_curriculums VALUES (3, 999, 'B', 'orphan', '', '');
            INSERT INTO paper_questions VALUES (1, 1, 1, 0, 5);
            INSERT INTO paper_questions VALUES (2, 1, 1, 0, 10);
            INSERT INTO paper_questions VALUES (3, 1, 999, 1, 5);
            """
        )


def test_migration_repairs_legacy_relationships_and_adds_constraints(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    backup_dir = tmp_path / "backups"
    _create_legacy_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", backup_dir / "schema_snapshots")
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 0
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["removed_question_curriculums"] == 2
    assert result["removed_paper_questions"] == 2
    assert result["backup"]
    assert list((backup_dir / "schema_snapshots").glob("*.sha256"))

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        assert connection.execute(
            "SELECT id, compulsory FROM question_curriculums"
        ).fetchall() == [(2, "new")]
        assert connection.execute(
            "SELECT id, score FROM paper_questions"
        ).fetchall() == [(2, 10)]
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 10
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO question_curriculums "
                "(question_id, version_code) VALUES (1, 'A')"
            )

        connection.execute("DELETE FROM questions WHERE id = 1")
        assert connection.execute("SELECT COUNT(*) FROM question_curriculums").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_questions").fetchone()[0] == 0


def test_migration_refuses_unknown_future_schema(tmp_path):
    database_path = tmp_path / "future.db"
    _create_legacy_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(f"PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION + 1}")

    engine = create_engine(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError, match="高于程序支持版本"):
        db_migrations.migrate_database(engine)


def test_migration_deduplicates_null_and_zero_paper_order_together(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "null-order.db"
    _create_legacy_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO paper_questions "
            "(id, paper_id, question_id, order_index, score) "
            "VALUES (4, 1, 1, NULL, 20)"
        )
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["removed_paper_questions"] == 3
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT id, order_index, score FROM paper_questions"
        ).fetchall() == [(4, 0, 20)]
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 20


def test_version_one_database_is_backed_up_and_recalculates_paper_totals(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "version-one.db"
    _create_legacy_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version=1")
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    result = db_migrations.migrate_database(engine)

    assert result["from_version"] == 1
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["backup"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT total_score FROM papers WHERE id = 1"
        ).fetchone()[0] == 10


def test_migration_ddl_failure_rolls_back_original_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "crash-safe.db"
    _create_legacy_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    def inject_failure(_conn, _cursor, statement, _parameters, _context, _many):
        if "ALTER TABLE question_curriculums__new RENAME" in statement:
            raise RuntimeError("injected migration failure")

    event.listen(engine, "before_cursor_execute", inject_failure)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        db_migrations.migrate_database(engine)
    event.remove(engine, "before_cursor_execute", inject_failure)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT id, compulsory FROM question_curriculums ORDER BY id"
        ).fetchall() == [(1, "old"), (2, "new"), (3, "orphan")]
        assert connection.execute(
            "SELECT id, score FROM paper_questions ORDER BY id"
        ).fetchall() == [(1, 5), (2, 10), (3, 5)]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "question_curriculums__new" not in table_names
        assert "paper_questions__new" not in table_names


def test_migration_refuses_partially_missing_schema(tmp_path):
    database_path = tmp_path / "partial.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    engine = create_engine(f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="缺少必要数据表"):
        db_migrations.migrate_database(engine)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_v1011_adds_redo_closure_schema(tmp_path, monkeypatch):
    """v1011：补 redo_attempts 表 + questions 七列（错题重做闭环），不改数据。"""

    database_path = tmp_path / "redo-closure.db"
    _create_legacy_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    db_migrations.migrate_database(engine)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "redo_attempts" in table_names

        question_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(questions)").fetchall()
        }
        assert {
            "correct_answer",
            "wrong_count",
            "redo_count",
            "redo_correct_streak",
            "mastery_status",
            "next_redo_due",
            "last_redo_at",
        } <= question_columns

        redo_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(redo_attempts)"
            ).fetchall()
        }
        assert {
            "student_id",
            "paper_id",
            "question_id",
            "attempt_no",
            "result",
            "option_mode",
            "subject",
            "attempted_at",
        } <= redo_columns

        # 只加列/建表，既有数据不动
        assert connection.execute("SELECT content FROM questions").fetchall() == [
            ("question",)
        ]

    # 幂等：重复迁移不报错
    again = db_migrations.migrate_database(engine)
    assert again["from_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert again["to_version"] == db_migrations.LATEST_SCHEMA_VERSION


def test_v1012_adds_manual_mastery_override_columns(tmp_path, monkeypatch):
    """v1012：questions 补 mastery_override / mastery_override_at（人工标注掌握度）。"""

    database_path = tmp_path / "mastery-override.db"
    _create_legacy_database(database_path)
    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "schema_snapshots"
    )
    engine = create_engine(f"sqlite:///{database_path}")

    db_migrations.migrate_database(engine)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        question_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(questions)").fetchall()
        }
        assert {"mastery_override", "mastery_override_at"} <= question_columns

        # 只加列、不回填：空串 / NULL 恰好等于「从未人工标注过」
        assert connection.execute(
            "SELECT mastery_override, mastery_override_at FROM questions"
        ).fetchall() == [("", None)]
        assert connection.execute("SELECT content FROM questions").fetchall() == [
            ("question",)
        ]

    again = db_migrations.migrate_database(engine)
    assert again["from_version"] == again["to_version"] == (
        db_migrations.LATEST_SCHEMA_VERSION
    )


def test_v1012_step_is_registered_in_the_chain():
    """这一步必须挂在版本链上，否则新库停在 1011、人工标注无处落脚。

    刻意**不写死** ``LATEST_SCHEMA_VERSION = 1012`` —— 那条断言每升一次版本都要
    回头改历史测试（本项目 v1010 的旧测试就是这么红的）。
    """

    source = (
        Path(__file__).resolve().parents[1] / "mathbank" / "db_migrations.py"
    ).read_text(encoding="utf-8")
    assert "def _add_mastery_override_columns_v1012(" in source
    assert "_add_mastery_override_columns_v1012(engine)" in source
    assert "elif current == 1011:" in source, "v1011 → v1012 的步骤必须挂上"
    assert db_migrations.LATEST_SCHEMA_VERSION >= 1012


def test_persistence_modules_remain_importable_on_python_310():
    project_root = Path(__file__).resolve().parents[1]
    module_paths = [
        project_root / "mathbank" / "backup.py",
        project_root / "mathbank" / "db_migrations.py",
        project_root / "mathbank" / "database.py",
        project_root / "mathbank" / "task_manager.py",
        project_root / "mathbank" / "health.py",
        project_root / "scripts" / "backup.py",
        project_root / "scripts" / "restore.py",
    ]

    for module_path in module_paths:
        source = module_path.read_text(encoding="utf-8")
        assert "datetime.UTC" not in source
        assert "dt.UTC" not in source


def test_init_db_refuses_future_schema_before_mutating_it(tmp_path, monkeypatch):
    database_path = tmp_path / "future-init.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            f"""
            PRAGMA user_version={db_migrations.LATEST_SCHEMA_VERSION + 1};
            CREATE TABLE future_only (id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO future_only (payload) VALUES ('preserve-me');
            """
        )
    before = database_path.read_bytes()
    future_engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", future_engine)

    with pytest.raises(RuntimeError, match="高于程序支持版本"):
        database_module.init_db()

    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT payload FROM future_only"
        ).fetchall() == [("preserve-me",)]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert table_names == {"future_only"}


@pytest.mark.parametrize("include_curriculum", [False, True])
def test_init_db_upgrades_known_questions_only_legacy_layouts(
    tmp_path, monkeypatch, include_curriculum
):
    database_path = tmp_path / "questions-only.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                question_type VARCHAR(50) DEFAULT 'single_choice',
                category_compulsory VARCHAR(100) DEFAULT '',
                category_chapter VARCHAR(100) DEFAULT '',
                category_knowledge VARCHAR(100) DEFAULT '',
                difficulty VARCHAR(50) DEFAULT 'medium',
                source VARCHAR(200) DEFAULT '',
                answer_markdown TEXT DEFAULT '',
                image_paths TEXT DEFAULT '[]',
                created_at DATETIME
            );
            INSERT INTO questions (
                id, content, question_type, category_compulsory,
                category_chapter, category_knowledge, difficulty,
                source, answer_markdown, image_paths, created_at
            ) VALUES (
                1, '历史题目', 'single_choice', '必修一',
                '第一章', '集合', 'medium', '', '', '[]', NULL
            );
            """
        )
        if include_curriculum:
            connection.executescript(
                """
                CREATE TABLE question_curriculums (
                    id INTEGER PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT ''
                );
                INSERT INTO question_curriculums VALUES
                    (1, 1, 'A', '必修一', '第一章', '集合');
                """
            )

    legacy_engine = create_engine(f"sqlite:///{database_path}")
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    database_module.init_db()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            db_migrations.LATEST_SCHEMA_VERSION
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert db_migrations.REQUIRED_TABLES.issubset(table_names)
        assert connection.execute(
            "SELECT question_id, version_code FROM question_curriculums"
        ).fetchall() == [(1, "A")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert list(snapshot_dir.glob("*.db"))
    assert list(snapshot_dir.glob("*.sha256"))
    legacy_engine.dispose()


def test_init_db_rejects_damaged_questions_table_before_creating_missing_tables(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "damaged.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
    damaged_engine = create_engine(f"sqlite:///{database_path}")
    snapshot_dir = tmp_path / "schema_snapshots"
    monkeypatch.setattr(database_module, "engine", damaged_engine)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", snapshot_dir)

    with pytest.raises(RuntimeError, match="缺少核心字段"):
        database_module.init_db()

    with sqlite3.connect(database_path) as connection:
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        } == {"questions"}
    assert not snapshot_dir.exists()
    damaged_engine.dispose()
