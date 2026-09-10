"""题源规约「交给 AI」的两条链路契约测试。

覆盖两块：

1. **A 规约注入提示词** —— 提示词里的题源规约由 ``source_normalize`` 单一事实源
   动态拼装，测试锁定「提示词与代码规约不漂移」「示例本身合规」「旧的反向指令
   （要求模型删除分隔符）已彻底消失」。
2. **B LLM 兜底归一** —— 规则引擎无力规整的全新来源交给小模型加工；测试锁定
   「默认不开」「结果必须过规约校验」「失败静默降级」「缓存不重复调用」
   「学到的映射落盘且保留用户注释字段」。
3. **C 落盘隔离** —— 学习结果只能写进重定向后的临时文件，绝不污染仓库内被提交的
   示例模板与真实 data/ 映射（历史上曾被测试写坏）。

全部用例不触网：LLM 调用 monkeypatch 在 ``post_chat_completion`` 这一层（保留真实的
规约校验闸门）；只有专门验证「不重复调用 / 异常降级」的用例才直接替换 ``_call_llm``。
"""

import json
import os
import pathlib

import pytest

from mathbank import source_ai_fallback
from mathbank.prompts import (
    build_classification_system_prompt,
    build_pdf_parse_system_prompt,
)
from mathbank.source_normalize import (
    CANONICAL_MAP,
    _SOURCE_PATTERNS,
    _SOURCE_RULE_EXAMPLES,
    _SOURCE_RULE_TEMPLATES,
    build_source_rule_prompt,
    is_canonical_source,
    normalize_source,
)


@pytest.fixture(autouse=True)
def _isolate_state():
    """清进程内缓存，并还原 AI 学到的映射条目，避免污染其它测试。"""
    source_ai_fallback.reset_cache()
    before = set(CANONICAL_MAP)
    yield
    source_ai_fallback.reset_cache()
    for key in set(CANONICAL_MAP) - before:
        CANONICAL_MAP.pop(key, None)


# ---------------------------------------------------------------------------
# A. 规约注入提示词
# ---------------------------------------------------------------------------


def test_rule_templates_match_canonical_patterns():
    """提示词里的六类模板必须与规约判定 _SOURCE_PATTERNS 一一对应（防漂移）。"""
    assert [n for n, _ in _SOURCE_RULE_TEMPLATES] == [n for n, _ in _SOURCE_PATTERNS]


def test_rule_prompt_mentions_every_canonical_category():
    prompt = build_source_rule_prompt()
    for name, _ in _SOURCE_PATTERNS:
        assert name in prompt


def test_prompt_examples_are_canonical_and_idempotent():
    """示例的规范值必须真的合规，且被归一函数原样返回（否则示例本身在教错）。"""
    for _raw, canon in _SOURCE_RULE_EXAMPLES:
        assert is_canonical_source(canon), f"示例不合规：{canon}"
        assert normalize_source(canon) == canon


def test_rule_prompt_teaches_spaced_separator_not_removal():
    prompt = build_source_rule_prompt()
    assert " · " in prompt
    # 旧提示词要求模型「去掉 · 等分隔符」，与规约完全相反，绝不能再现
    for banned in ("去掉", "清洗为", "删除分隔符"):
        assert banned not in prompt


def test_rule_prompt_forbids_fabrication_and_latex():
    prompt = build_source_rule_prompt()
    assert "严禁编造" in prompt
    assert "LaTeX" in prompt


def test_rule_prompt_lists_known_schools_and_dedupes_paper_types():
    from mathbank.source_normalize import _known_paper_types, _known_schools

    prompt = build_source_rule_prompt()
    # 断言「已知学校」行与映射表反推结果严格一致（而不是碰巧被示例里的校名蹭中）
    schools = _known_schools()
    assert schools, "示例映射里至少应能反推出一个学校"
    assert f"已知学校（优先使用，勿自造）：{'、'.join(schools)}" in prompt
    assert "已知卷种" in prompt
    # 卷种跨年份重复（多个年份都有「上海」），提示词里必须已去重
    paper_types = _known_paper_types()
    assert paper_types == sorted(set(paper_types))


def test_parse_prompt_embeds_source_rules_and_title_anchor():
    prompt = build_pdf_parse_system_prompt(
        {"必修一": {}}, False, paper_title="成都七中高一上期中"
    )
    assert "题目来源（source）命名规约" in prompt
    assert "本卷来源唯一依据" in prompt
    assert "成都七中高一上期中" in prompt


def test_parse_prompt_without_title_has_no_title_anchor():
    prompt = build_pdf_parse_system_prompt({"必修一": {}}, False)
    assert "题目来源（source）命名规约" in prompt
    assert "本卷来源唯一依据" not in prompt


def test_classification_prompt_embeds_rules_and_drops_legacy_instruction():
    prompt = build_classification_system_prompt({"必修一": {}})
    assert "题目来源（source）命名规约" in prompt
    # 旧指令的反面教材（把分隔符洗掉）必须彻底消失
    assert "2024全国高考真题" not in prompt
    assert "清洗为" not in prompt


# ---------------------------------------------------------------------------
# B. LLM 兜底归一
# ---------------------------------------------------------------------------


def _patch_llm(monkeypatch, content):
    """把 LLM 打在假 HTTP 层（而非替换 _call_llm），保留真实的规约校验闸门。

    若直接 monkeypatch ``_call_llm``，就会跳过「输出必须过 is_canonical_source」
    这条最重要的防线，测试将失去意义。
    """

    class _Resp:
        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Provider:
        api_key = "test-key"
        model_name = "fake/model"
        api_base = "https://example.invalid/v1"
        provider_label = "fake"
        credential_label = "fake"

    monkeypatch.setattr(
        source_ai_fallback,
        "decide_classify_model",
        lambda use_free: {"provider": _Provider(), "raw_model": "fake/model"},
    )
    monkeypatch.setattr(
        source_ai_fallback,
        "post_chat_completion",
        lambda provider, payload, **kwargs: _Resp(),
    )


def test_default_is_pure_rules_and_never_calls_llm(monkeypatch):
    calls = {"n": 0}

    def fake(raw, fallback_title=None):
        calls["n"] += 1
        return "不应该被采纳"

    monkeypatch.setattr(source_ai_fallback, "_call_llm", fake)
    raw = "某个完全没见过的来源XYZ"
    assert normalize_source(raw) == raw
    assert calls["n"] == 0


def test_ai_fallback_adopted_when_rules_cannot_structure(monkeypatch):
    canon = "高一上 · 期中 · 成都七中 · 2025-2026学年"
    _patch_llm(monkeypatch, canon)
    assert (
        normalize_source("2025-2026学年成都七中高一上期中考试数学", allow_ai=True) == canon
    )


def test_ai_fallback_cleans_code_fence_before_adopting(monkeypatch):
    _patch_llm(monkeypatch, "```\n2025 · 江苏盐城 · 模拟\n```")
    assert (
        normalize_source("2025盐城一模全新WWW", allow_ai=True) == "2025 · 江苏盐城 · 模拟"
    )


def test_ai_fallback_rejects_non_canonical_output(monkeypatch):
    """用非法分隔符的输出必须被丢弃，退回规则结果（这是防污染的关键闸门）。"""
    _patch_llm(monkeypatch, "成都七中/高一上/期中")
    raw = "某个全新来源ABC"
    assert normalize_source(raw, allow_ai=True) == raw


def test_ai_fallback_rejects_empty_output(monkeypatch):
    _patch_llm(monkeypatch, "")
    raw = "又一个新的来源KKK"
    assert normalize_source(raw, allow_ai=True) == raw


def test_ai_fallback_rejects_latex_hallucination(monkeypatch):
    _patch_llm(monkeypatch, "\\begin{tikzpicture}[scale=0.8]")
    raw = "再一个新的来源LLL"
    assert normalize_source(raw, allow_ai=True) == raw


def test_ai_fallback_is_cached_per_source(monkeypatch):
    calls = {"n": 0}

    def fake(raw, fallback_title=None):
        calls["n"] += 1
        return "2026 · 四川高三第一次教学质量联合测评 · 联考"

    monkeypatch.setattr(source_ai_fallback, "_call_llm", fake)
    raw = "四川省2026届高三第一次教学质量联合测评ZZZ"
    normalize_source(raw, allow_ai=True)
    normalize_source(raw, allow_ai=True)
    assert calls["n"] == 1


def test_ai_fallback_survives_exception(monkeypatch):
    def boom(raw, fallback_title=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(source_ai_fallback, "_call_llm", boom)
    raw = "另一个全新来源QQQ"
    assert normalize_source(raw, allow_ai=True) == raw


def test_ai_fallback_disabled_by_env_kill_switch(monkeypatch):
    monkeypatch.setenv("SOURCE_AI_FALLBACK", "0")
    monkeypatch.setattr(source_ai_fallback, "_call_llm", lambda raw, fb=None: "教辅 · 某某")
    raw = "全新来源ENV"
    assert normalize_source(raw, allow_ai=True) == raw


def test_ai_fallback_skips_already_canonical_source(monkeypatch):
    def fail(raw, fallback_title=None):
        raise AssertionError("已合规的来源不应触发 LLM")

    monkeypatch.setattr(source_ai_fallback, "_call_llm", fail)
    canon = "2025 · 江苏盐城 · 模拟"
    assert normalize_source(canon, allow_ai=True) == canon


def test_learned_entry_persists_to_user_map_and_keeps_comments(monkeypatch, tmp_path):
    cfg = tmp_path / "source_canonical_map.json"
    cfg.write_text(
        json.dumps(
            {
                "_doc": "keep me",
                "school_entries": {"_doc_inner": "keep too", "已有条目": "教辅 · 已有条目"},
                "school_aliases": {"成外": "成都外国语学校"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(source_ai_fallback, "_user_map_path", lambda: cfg)
    canon = "教辅 · 高一集合运算小测2"
    monkeypatch.setattr(source_ai_fallback, "_call_llm", lambda raw, fb=None: canon)

    raw = "高一集合运算小测2"
    assert normalize_source(raw, allow_ai=True) == canon

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["school_entries"][raw] == canon
    assert data["_doc"] == "keep me"
    assert data["school_entries"]["_doc_inner"] == "keep too"
    assert data["school_aliases"]["成外"] == "成都外国语学校"


def test_persist_does_not_overwrite_user_authored_entry(monkeypatch, tmp_path):
    cfg = tmp_path / "source_canonical_map.json"
    cfg.write_text(
        json.dumps({"school_entries": {"某来源": "教辅 · 用户手工指定"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(source_ai_fallback, "_user_map_path", lambda: cfg)
    source_ai_fallback._persist("某来源", "教辅 · AI 猜的")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["school_entries"]["某来源"] == "教辅 · 用户手工指定"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "```\n高一上 · 期中 · 成都七中 · 2025-2026学年\n```",
            "高一上 · 期中 · 成都七中 · 2025-2026学年",
        ),
        ('"2025 · 江苏盐城 · 模拟"', "2025 · 江苏盐城 · 模拟"),
        ("2025 · 江苏盐城 · 模拟\n（理由：标题里有地区）", "2025 · 江苏盐城 · 模拟"),
        ("", ""),
    ],
)
def test_clean_output(raw, expected):
    assert source_ai_fallback._clean_output(raw) == expected


# ---------------------------------------------------------------------------
# C. 落盘隔离 —— 学习结果只能写进指定位置，绝不污染仓库里的映射文件
# ---------------------------------------------------------------------------
# 背景（真实踩过的坑）：早期版本用 ``MATHBANK_SOURCE_MAP`` 的只读覆盖文件当落盘
# 目标，测试一跑就把 AI 学到的条目写进了被提交的 ``source_canonical_map.example
# .json``，还顺手 ``json.dumps`` 重排了整份文件。那些垃圾条目随后又被 Tier 1
# 提前命中，反过来让本文件的用例产生「一会儿命中一会儿不命中」的假故障。

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_write_target_is_outside_repo(monkeypatch):
    """测试期间落盘目标必须被重定向到仓库之外。"""
    target = source_ai_fallback._user_map_path()
    assert target is not None
    assert not str(target).startswith(str(_REPO_ROOT)), f"落盘目标在仓库内：{target}"


def test_write_path_never_falls_back_to_read_only_override(monkeypatch, tmp_path):
    """未设 MATHBANK_SOURCE_MAP_WRITE 时，写目标须为真实搜索路径，
    绝不能退化成 MATHBANK_SOURCE_MAP 指向的只读模板。"""
    from mathbank.source_normalize import (
        _USER_MAP_SEARCH_PATHS,
        _resolve_writable_map_paths,
    )

    ro = tmp_path / "readonly.json"
    ro.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MATHBANK_SOURCE_MAP", str(ro))
    monkeypatch.delenv("MATHBANK_SOURCE_MAP_WRITE", raising=False)
    writable = _resolve_writable_map_paths()
    assert writable == list(_USER_MAP_SEARCH_PATHS)
    assert ro not in writable


def test_ai_fallback_does_not_mutate_repo_map_files(monkeypatch):
    """端到端护栏：跑一次「AI 学习成功」的归一，仓库内任何映射文件都不得变化，
    而学习结果确实落进了重定向后的临时文件。"""
    watched = [
        _REPO_ROOT / "source_canonical_map.example.json",
        _REPO_ROOT / "data" / "source_canonical_map.json",
    ]
    before = {p: (p.read_bytes() if p.is_file() else None) for p in watched}

    canon = "教辅 · 只应写进临时文件的条目"
    _patch_llm(monkeypatch, canon)
    raw = "只应写进临时文件的条目KKK"
    assert normalize_source(raw, allow_ai=True) == canon

    for path, snapshot in before.items():
        now = path.read_bytes() if path.is_file() else None
        assert now == snapshot, f"仓库映射文件被测试改动：{path}"

    scratch = pathlib.Path(os.environ["MATHBANK_SOURCE_MAP_WRITE"])
    assert json.loads(scratch.read_text(encoding="utf-8"))["school_entries"][raw] == canon
