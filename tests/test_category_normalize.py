"""拆题结果学段/章节/小节受控归一（normalize_category_fields）的单元测试。

背景：审查卡片下拉严格相等匹配受控教材目录，LLM 输出空值 / 别名
（「必修第一册」）/ 教辅专题名时三级下拉静默全空。本套用例固化
main.normalize_category_fields 的归一与知识点反推兜底行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from main import (
    _infer_comp_chap_from_knowledge,
    _match_chapter,
    _match_compulsory,
    _match_knowledge_in_section,
    _normalize_category_key,
    normalize_category_fields,
)

_CURRICULUM = json.load(
    open(
        Path(__file__).resolve().parent.parent
        / "mathbank" / "resources" / "curriculums" / "A.json",
        encoding="utf-8",
    )
)
# A 版：书 = 必修一/必修二/选修一/选修二/选修三
# 必修一章节含「2. 一元二次函数、方程和不等式」（小节含「2.2 基本不等式」）


def _q(**kw):
    return dict(kw)


# ---------- 归一化基元 ----------

def test_normalize_key_strips_number_prefix_and_fullwidth():
    assert _normalize_category_key("2. 一元二次函数、方程和不等式") == "一元二次函数,方程和不等式"
    assert _normalize_category_key("第一章 集合") == "集合"
    assert _normalize_category_key("（３）函数") == "函数"


# ---------- 学段 ----------

def test_compulsory_exact_pass_through():
    assert _match_compulsory("必修一", _CURRICULUM) == "必修一"


def test_compulsory_alias_first_volume():
    assert _match_compulsory("必修第一册", _CURRICULUM) == "必修一"
    assert _match_compulsory("选择性必修一", _CURRICULUM) == "选修一"


def test_compulsory_containment_unique_hit():
    assert _match_compulsory("人教A版必修一", _CURRICULUM) == "必修一"


def test_compulsory_garbage_returns_none():
    assert _match_compulsory("数辅专题", _CURRICULUM) is None
    assert _match_compulsory("", _CURRICULUM) is None


# ---------- 章节 ----------

def test_chapter_strips_number_prefix():
    raw = "一元二次函数、方程和不等式"
    assert _match_chapter(raw, "必修一", _CURRICULUM) == "2. 一元二次函数、方程和不等式"


def test_chapter_containment_unique():
    assert _match_chapter("三角函数", "必修一", _CURRICULUM) == "5. 三角函数"


def test_chapter_wrong_book_returns_none():
    # 必修二没有「三角函数」章（A 版），唯一命中不成立 → None
    assert _match_chapter("三角函数", "必修二", _CURRICULUM) is None


# ---------- 小节 ----------

def test_knowledge_exact_and_unique_containment():
    assert _match_knowledge_in_section(
        "2.2 基本不等式", "必修一", "2. 一元二次函数、方程和不等式", _CURRICULUM
    ) == "2.2 基本不等式"
    assert _match_knowledge_in_section(
        "基本不等式", "必修一", "2. 一元二次函数、方程和不等式", _CURRICULUM
    ) == "2.2 基本不等式"


# ---------- 知识点反推 ----------

def test_infer_comp_chap_unique_winner():
    assert _infer_comp_chap_from_knowledge(
        ["基本不等式"], _CURRICULUM
    ) == ("必修一", "2. 一元二次函数、方程和不等式")


def test_infer_multi_hit_returns_none():
    # 「函数」这类宽泛标签会命中多个章节 → 放弃
    assert _infer_comp_chap_from_knowledge(["函数"], _CURRICULUM) is None


# ---------- 端到端（模拟 LLM 各类坏输出） ----------

def test_normalize_fields_alias_and_prefix():
    questions = [
        _q(compulsory="必修第一册", chapter="第二章 一元二次函数、方程和不等式",
           knowledge_list=["基本不等式"]),
    ]
    stats = normalize_category_fields(questions, _CURRICULUM)
    q = questions[0]
    assert q["category_compulsory"] == "必修一"
    assert q["category_chapter"] == "2. 一元二次函数、方程和不等式"
    assert q["category_knowledge"] == "2.2 基本不等式"
    assert stats["comp"] == 1 and stats["chap"] == 1 and stats["know"] == 1


def test_normalize_fields_empty_llm_output_rescued_by_knowledge():
    questions = [
        _q(compulsory="", chapter="", knowledge_list=["基本不等式", "消元法"]),
    ]
    stats = normalize_category_fields(questions, _CURRICULUM)
    q = questions[0]
    assert q["category_compulsory"] == "必修一"
    assert q["category_chapter"] == "2. 一元二次函数、方程和不等式"
    assert stats["comp"] == 1


def test_normalize_fields_unmatchable_stays_empty():
    questions = [
        _q(compulsory="数辅专题三", chapter="必刷专题", knowledge_list=[]),
    ]
    stats = normalize_category_fields(questions, _CURRICULUM)
    q = questions[0]
    # 无法受控命中 → 留空交用户手选，绝不猜值入库
    assert not q.get("category_compulsory")
    assert stats["comp"] == 0
    assert stats["comp_raw"].get("数辅专题三") == 1


def test_normalize_fields_comp_match_but_chap_inferred():
    questions = [
        _q(compulsory="必修一", chapter="", knowledge_list=["基本不等式"]),
    ]
    stats = normalize_category_fields(questions, _CURRICULUM)
    q = questions[0]
    assert q["category_compulsory"] == "必修一"
    assert q["category_chapter"] == "2. 一元二次函数、方程和不等式"
    assert stats["chap"] == 1


def test_normalize_fields_exact_values_untouched():
    questions = [
        _q(category_compulsory="必修一", category_chapter="5. 三角函数",
           category_knowledge="", knowledge_list=[]),
    ]
    stats = normalize_category_fields(questions, _CURRICULUM)
    q = questions[0]
    assert q["category_compulsory"] == "必修一"
    assert q["category_chapter"] == "5. 三角函数"
    assert stats["comp"] == 1 and stats["chap"] == 1
