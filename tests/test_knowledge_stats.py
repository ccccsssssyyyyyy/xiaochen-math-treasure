"""Tests for ``GET /api/knowledge-stats`` aggregation endpoint.

2026-09-04 代码审查 E1 修复：原 ``editor.js:890`` 为数知识点分布却拉整章题目
完整记录（最大章节 228.8 KB vs 聚合后 0.68 KB，~339× 浪费）。新增
``/api/knowledge-stats`` 服务端 GROUP BY 直接返回 {category_knowledge: count}。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.database import Question  # noqa: E402


# ---------- 必填字段最小 Question 工厂 ----------

def _make_question(
    content,
    category_compulsory="",
    category_chapter="",
    category_knowledge="",
):
    """构造最小可入库 Question，category_* 字段按需填充。"""
    return Question(
        content=content,
        question_type="single_choice",
        category_compulsory=category_compulsory,
        category_chapter=category_chapter,
        category_knowledge=category_knowledge,
    )


# ---------- 核心聚合 ----------

def test_knowledge_stats_groups_by_category_knowledge(client, db_session):
    """基础聚合：3 题（2+1 知识点）→ 返回 {知识点: 题数}。

    同时验证：
    - 空 category_knowledge 归到「未细分知识点」键（与 list_questions 一致）
    - 不传 compulsory/chapter 时全库统计
    """
    db_session.add_all([
        _make_question("q1", category_knowledge="集合的概念"),
        _make_question("q2", category_knowledge="集合的概念"),
        _make_question("q3", category_knowledge="集合的运算"),
    ])
    db_session.commit()

    resp = client.get("/api/knowledge-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"集合的概念": 2, "集合的运算": 1}


def test_knowledge_stats_empty_knowledge_becomes_placeholder(client, db_session):
    """空 category_knowledge（库内默认 ""）→ 归到「未细分知识点」。"""
    db_session.add_all([
        _make_question("q1"),  # 默认 ""
        _make_question("q2"),  # 默认 ""
    ])
    db_session.commit()

    resp = client.get("/api/knowledge-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"未细分知识点": 2}


# ---------- 过滤 ----------

def test_knowledge_stats_filters_by_compulsory_and_chapter(client, db_session):
    """compulsory + chapter 联合过滤：只返回匹配章节的知识点分布。"""
    db_session.add_all([
        # 必修一 · 集合与常用逻辑用语
        _make_question("q1", category_compulsory="必修一",
                       category_chapter="集合与常用逻辑用语",
                       category_knowledge="集合的概念"),
        # 必修一 · 函数
        _make_question("q2", category_compulsory="必修一",
                       category_chapter="函数",
                       category_knowledge="函数的概念"),
        # 必修二
        _make_question("q3", category_compulsory="必修二",
                       category_chapter="平面向量",
                       category_knowledge="向量的概念"),
    ])
    db_session.commit()

    # 只查必修一 · 集合
    resp = client.get(
        "/api/knowledge-stats?compulsory=必修一&chapter=集合与常用逻辑用语"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"集合的概念": 1}, f"应只含 q1，实际 {data}"


def test_knowledge_stats_counts_related_curriculums_questions(client, db_session):
    """融合题：主章节不同但 related_curriculums 含目标章节 → 也被计入。

    与 list_questions 行为对齐（用 json_each 查关联章节）。
    """
    db_session.add_all([
        # 主章节在函数，但关联章节含「集合与常用逻辑用语」
        _make_question(
            "q1",
            category_compulsory="必修一",
            category_chapter="函数",
            category_knowledge="函数的单调性",
        ),
    ])
    db_session.commit()

    # 直接修改 related_curriculums（JSON 字段）以构造融合题
    q = db_session.query(Question).first()
    q.related_curriculums = '[{"chapter": "集合与常用逻辑用语", "knowledge": "集合的概念"}]'
    db_session.commit()

    # 查「集合与常用逻辑用语」应能命中此融合题
    resp = client.get(
        "/api/knowledge-stats?compulsory=必修一&chapter=集合与常用逻辑用语"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"函数的单调性": 1}, f"融合题应被聚合到目标章节，实际 {data}"


def test_knowledge_stats_returns_empty_dict_on_no_data(client):
    """空库：无参数调用 → 空字典（不是错误）。"""
    resp = client.get("/api/knowledge-stats")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_knowledge_stats_returns_empty_dict_when_no_match(client, db_session):
    """有数据但章节过滤无匹配 → 空字典。"""
    db_session.add(_make_question(
        "q1", category_compulsory="必修一", category_chapter="函数",
        category_knowledge="函数的概念",
    ))
    db_session.commit()

    resp = client.get(
        "/api/knowledge-stats?compulsory=必修一&chapter=不存在的章节"
    )
    assert resp.status_code == 200
    assert resp.json() == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))