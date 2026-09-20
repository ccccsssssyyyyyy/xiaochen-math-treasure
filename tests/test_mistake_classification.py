# -*- coding: utf-8 -*-
"""错题记录「分类信息」（v1010）—— 审校页可编辑，入库时带进题库。

用户 2026-09-15 拍板：错题本审校页中栏做成与题库录入表单同款的分类信息，
并**真的落库**，入库时搬进 ``questions``。

迁移前这一步是断的：``_import_mistake_record_to_bank`` 把
``category_compulsory`` / ``category_chapter`` / ``category_knowledge`` 三列
**写死成空字符串**，于是错题进题库后学段/章节/小节全空，在题库的「章节」筛选里
根本搜不到 —— 错题本这条录入口子等于半废。本文件锁住这条链路的两端：

    审校页 PUT → mistake_records 六列 → 入库 → questions + question_curriculums

断言分四组：迁移（补列/幂等/版本号）、序列化（下发给前端）、审校保存（PUT 契约）、
入库映射（含来源优先级与「未分类」兜底）。全部走真实接口与真实映射函数，
不用 mock 顶替 —— 用 mock 顶的那一段正是最容易出错的一段。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from mathbank import db_migrations
from mathbank.database import (
    Base,
    MistakeBatch,
    MistakeRecord,
    Question,
    QuestionCurriculum,
    Student,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: v1010 补上的六列，逐字对齐 questions 表
CLASSIFICATION_COLUMNS = (
    "source",
    "category_compulsory",
    "category_chapter",
    "category_knowledge",
    "related_curriculums",
    "tags",
)


def _main():
    import main

    return main


# --------------------------------------------------------------- 1) 迁移


@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    """建出「当前结构但缺 v1010 六列」的库，模拟升级前的用户库。"""

    monkeypatch.setattr(
        db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snapshots"
    )
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        for column in CLASSIFICATION_COLUMNS:
            connection.exec_driver_sql(
                f"ALTER TABLE mistake_records DROP COLUMN {column}"
            )
        connection.exec_driver_sql("PRAGMA user_version=1009")
    return engine


def test_v1010_adds_classification_columns(legacy_engine) -> None:
    """升级后六列齐备，且版本号一路推到最新。

    刻意**不写死**终态版本号：迁移是链式的，从 1009 起跳的库会一直走到
    ``LATEST_SCHEMA_VERSION``。硬编 1010 会在每次新增迁移时误红
    —— 项目已因这条断言红过一轮（1009 → 1010 时）。
    """

    before = {c["name"] for c in inspect(legacy_engine).get_columns("mistake_records")}
    assert not (set(CLASSIFICATION_COLUMNS) & before), "夹具没造出「缺列」的旧库"

    result = db_migrations.migrate_database(legacy_engine)
    assert result["from_version"] == 1009
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    assert result["added_mistake_classification_columns"] == 6

    after = {c["name"] for c in inspect(legacy_engine).get_columns("mistake_records")}
    for column in CLASSIFICATION_COLUMNS:
        assert column in after, f"{column} 没补上"


def test_v1010_does_not_touch_existing_rows(legacy_engine) -> None:
    """存量行只拿到默认值，不被回填改语义 —— 空串即「未分类」。"""

    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO students (name, grade) VALUES ('测试学生', '高一')"
        )
        connection.exec_driver_sql(
            "INSERT INTO mistake_batches (student_id, subject, title) "
            "VALUES (1, 'math', '旧批次')"
        )
        connection.exec_driver_sql(
            "INSERT INTO mistake_records (batch_id, subject, content) "
            "VALUES (1, 'math', '旧记录')"
        )

    db_migrations.migrate_database(legacy_engine)

    with legacy_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT source, category_compulsory, category_chapter, "
                "category_knowledge, related_curriculums, tags "
                "FROM mistake_records WHERE id = 1"
            )
        ).fetchone()
    assert row == ("", "", "", "", "[]", "")
    with legacy_engine.connect() as connection:
        content = connection.execute(
            text("SELECT content FROM mistake_records WHERE id = 1")
        ).scalar_one()
    assert content == "旧记录", "旧数据被迁移改动过"


def test_v1010_is_idempotent(legacy_engine) -> None:
    """重复执行必须是无操作 —— 否则每次重启都去 ALTER 一遍。"""

    db_migrations.migrate_database(legacy_engine)
    second = db_migrations.migrate_database(legacy_engine)
    latest = db_migrations.LATEST_SCHEMA_VERSION
    assert second["from_version"] == second["to_version"] == latest
    assert "added_mistake_classification_columns" not in second


def test_v1010_step_is_registered_in_the_chain() -> None:
    """迁移函数必须挂进 migrate_database 的版本链，否则新库永远停在 1009。"""

    source = (PROJECT_ROOT / "mathbank" / "db_migrations.py").read_text(encoding="utf-8")
    # 版本号只校验「与常量一致 + 不低于本步」，不钉死具体数字
    assert f"LATEST_SCHEMA_VERSION = {db_migrations.LATEST_SCHEMA_VERSION}" in source
    assert db_migrations.LATEST_SCHEMA_VERSION >= 1010, "v1010 之后不该出现版本回退"
    assert "def _add_mistake_classification_columns_v1010(" in source
    assert "elif current == 1009:" in source
    assert "_add_mistake_classification_columns_v1010(engine)" in source


# ------------------------------------------------------- 2) 下发与保存契约


def _seed_record(db_session, **fields):
    student = Student(name="测试学生", grade="高一")
    db_session.add(student)
    db_session.commit()
    batch = MistakeBatch(
        student_id=student.id, subject="math", title="分类测试", status="reviewing"
    )
    db_session.add(batch)
    db_session.commit()
    record = MistakeRecord(
        batch_id=batch.id,
        student_id=student.id,
        subject="math",
        content="已知 $x^2-3x+2=0$，求 $x$。",
        grad_status="incorrect",
        include_in_handout=True,
    )
    for key, value in fields.items():
        setattr(record, key, value)
    db_session.add(record)
    db_session.commit()
    return batch, record


def test_record_payload_exposes_classification(db_session) -> None:
    """审校页靠下发字段回填表单 —— 少一个键，界面上就是空的。"""

    _, record = _seed_record(db_session)
    payload = record.to_dict()
    for key in CLASSIFICATION_COLUMNS:
        assert key in payload, f"to_dict 少了 {key}"
    assert payload["related_curriculums"] == [], "关联章节应是列表，不是裸字符串"


def test_put_record_persists_classification(db_session, client) -> None:
    """审校页保存走 PUT —— 六个字段必须原样落库。"""

    main = _main()
    _, record = _seed_record(db_session)
    related = [{"compulsory": "必修第一册", "chapter": "第三章", "knowledge": "函数"}]

    response = client.put(
        f"/api/mistakes/records/{record.id}",
        json={
            "source": "2025 全国甲卷",
            "category_compulsory": "必修第一册",
            "category_chapter": "第三章 函数",
            "category_knowledge": "函数的单调性",
            "related_curriculums": related,
            "tags": "高一, 期中",
            "knowledge_tags": "函数单调性",
            "solve_method": "定义法",
        },
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"

    db_session.expire_all()
    saved = db_session.query(MistakeRecord).filter(MistakeRecord.id == record.id).first()
    assert saved.source == "2025 全国甲卷"
    assert saved.category_compulsory == "必修第一册"
    assert saved.category_chapter == "第三章 函数"
    assert saved.category_knowledge == "函数的单调性"
    assert saved.tags == "高一, 期中"
    assert json.loads(saved.related_curriculums) == related
    # 原有的分类字段一并写通（同一次保存里不能顾此失彼）
    assert saved.knowledge_tags
    assert saved.solve_method
    # 回传体也要带上，前端才能立刻回填
    assert body["record"]["category_chapter"] == "第三章 函数"
    assert body["record"]["related_curriculums"] == related


def test_put_record_rejects_garbage_related_curriculums(db_session, client) -> None:
    """关联章节是 JSON 数组：乱填不能把库写坏，退化成空数组。"""

    main = _main()
    _, record = _seed_record(db_session)
    response = client.put(
        f"/api/mistakes/records/{record.id}",
        json={"related_curriculums": "这不是 JSON"},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    saved = db_session.query(MistakeRecord).filter(MistakeRecord.id == record.id).first()
    assert json.loads(saved.related_curriculums) == []


# ------------------------------------------------------------- 3) 入库映射


def test_import_maps_classification_into_bank(db_session, tmp_path, monkeypatch) -> None:
    """入库把六列搬进 questions 与 question_curriculums —— 这是本轮修复的正题。"""

    main = _main()
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))
    related = [{"compulsory": "必修第一册", "chapter": "第二章", "knowledge": "不等式"}]
    batch, record = _seed_record(
        db_session,
        question_type="single_choice",
        difficulty="easy_error",
        source="2025 全国甲卷",
        category_compulsory="必修第一册",
        category_chapter="第三章 函数",
        category_knowledge="函数的单调性",
        related_curriculums=json.dumps(related, ensure_ascii=False),
        tags="高一, 期中",
        knowledge_tags="函数单调性",
        solve_method="定义法",
    )

    result = main._import_mistake_record_to_bank(db_session, record, force=True)
    assert result["status"] == "imported", result
    db_session.commit()

    question = (
        db_session.query(Question).filter(Question.id == result["question_id"]).first()
    )
    assert question is not None
    assert question.category_compulsory == "必修第一册"
    assert question.category_chapter == "第三章 函数"
    assert question.category_knowledge == "函数的单调性"
    assert question.tags == "高一, 期中"
    assert json.loads(question.related_curriculums) == related
    assert question.origin == "mistake"
    assert question.source, "来源不能为空"

    mapping = (
        db_session.query(QuestionCurriculum)
        .filter(QuestionCurriculum.question_id == question.id)
        .first()
    )
    assert mapping is not None, "缺 question_curriculums 映射，章节筛选会漏掉这道题"
    assert mapping.compulsory == "必修第一册"
    assert mapping.chapter == "第三章 函数"
    assert mapping.knowledge == "函数的单调性"


def test_import_prefers_handwritten_source(db_session, tmp_path, monkeypatch) -> None:
    """来源优先级：审校页手填 > 上次入库快照 > 批次标题 > 扫描文件名。

    手填排第一是本轮的关键：批次文件名常是 ``IMG_2043.jpg``，它会把手填的
    「2025 全国甲卷」顶掉。
    """

    main = _main()
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    batch, record = _seed_record(db_session, source="2025 全国甲卷")
    batch.source_name = "IMG_2043.jpg"
    batch.title = "扫描批次"
    db_session.commit()

    result = main._import_mistake_record_to_bank(db_session, record, force=True)
    assert result["status"] == "imported", result
    question = db_session.query(Question).filter(Question.id == result["question_id"]).first()
    assert "IMG_2043" not in question.source, "扫描文件名把手填来源顶掉了"
    assert "2025" in question.source, f"手填来源没生效：{question.source!r}"


def test_import_falls_back_to_uncategorized(db_session, tmp_path, monkeypatch) -> None:
    """没填学段/章节时归入「未分类」，与题库录入表单同口径。

    留空会让这道题在题库的「章节」筛选里两头不沾 —— 既不属于任何章节，
    也不属于未分类桶，等于从筛选里消失。
    """

    main = _main()
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))
    _, record = _seed_record(db_session)

    result = main._import_mistake_record_to_bank(db_session, record, force=True)
    assert result["status"] == "imported", result
    # 测试会话是 autoflush=False（conftest），函数里的 db.add 靠真实端点末尾的 commit
    # 落盘；这里直接调函数，必须自己提交，否则查不到刚 add 的映射行（第 3 组的用例
    # 就是这么红的，排查成本比写这条注释高得多）。
    db_session.commit()
    question = db_session.query(Question).filter(Question.id == result["question_id"]).first()
    assert question.category_compulsory == "未分类"
    assert question.category_chapter == "未分类"
    mapping = (
        db_session.query(QuestionCurriculum)
        .filter(QuestionCurriculum.question_id == question.id)
        .first()
    )
    assert mapping.compulsory == "未分类" and mapping.chapter == "未分类"


# --------------------------------------------------------- 4) 别把输入弄丢


def test_carry_over_fields_include_classification() -> None:
    """重切会重建记录：分类六列必须在继承清单里，否则审校页填的分类会被重切吃掉。"""

    main = _main()
    for field in CLASSIFICATION_COLUMNS:
        assert field in main.MISTAKE_CARRY_OVER_FIELDS, f"重切会丢掉 {field}"
