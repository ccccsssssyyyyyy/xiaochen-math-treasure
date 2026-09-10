"""get_seq_mapping（题目序号映射）的语义与性能回归。

⚠️ 必须走 ``db_session`` fixture：conftest 已把 ``database.SessionLocal`` 换成
内存 SQLite，而建表/删表由 ``db_session`` 负责。此前的写法直接 ``SessionLocal()``
却不请求该 fixture，拿到的是「没有 questions 表」的空库，必然
``OperationalError: no such table: questions``（测试脚手架缺陷，非被测代码问题）。
"""

import random

import pytest

from mathbank.database import Question
from main import get_seq_mapping

# 行数取 12：既让「小样本 10 条」是真正的子集，也让「大样本 100 条」触发截断到全表
_SEED_ROWS = 12


def _seed(db, n: int = _SEED_ROWS) -> None:
    """写入 n 道最小可用题目。题目序号以 id 升序为准。"""
    db.add_all([Question(content=f"测试题 {i + 1}") for i in range(n)])
    db.commit()


def _ordered_ids(db) -> list[int]:
    return [q.id for q in db.query(Question.id).order_by(Question.id.asc()).all()]


def test_get_seq_mapping_full_table_matches_expected(db_session):
    _seed(db_session)
    ids = _ordered_ids(db_session)
    assert len(ids) == _SEED_ROWS, "种子数据应全部落库"
    expected = {qid: idx + 1 for idx, qid in enumerate(ids)}
    assert get_seq_mapping(db_session, None) == expected


def test_get_seq_mapping_small_subset_matches_expected(db_session):
    _seed(db_session)
    ids = _ordered_ids(db_session)
    sample = random.sample(ids, min(10, len(ids)))
    expected = {qid: idx + 1 for idx, qid in enumerate(ids) if qid in sample}
    assert get_seq_mapping(db_session, sample) == expected


def test_get_seq_mapping_large_subset_matches_expected(db_session):
    _seed(db_session)
    ids = _ordered_ids(db_session)
    subset = random.sample(ids, min(100, len(ids)))
    expected = {qid: idx + 1 for idx, qid in enumerate(ids) if qid in subset}
    assert get_seq_mapping(db_session, subset) == expected


def test_get_seq_mapping_empty_returns_empty(db_session):
    _seed(db_session)
    assert get_seq_mapping(db_session, []) == {}


def test_get_seq_mapping_ignores_unknown_ids(db_session):
    """传入库中不存在的 id 时返回空映射（筛不出行 → 不编号），且不报错。"""
    _seed(db_session)
    assert get_seq_mapping(db_session, [999999]) == {}
