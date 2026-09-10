"""后端来源归一钩子回归测试。

覆盖 mathbank.source_normalize.normalize_source：
- 空值 / None -> '未知'
- 库内原始写法（裸写 / 文件名形式）-> 规约写法
- 规约终态幂等（Tier1 直接命中）
- 全新输入的 Tier2 结构兜底
- 映射闭合（每个规约终态自身也是键，保证脚本可重复执行 & 钩子幂等）

注：个人化映射与学校别名由 tests/conftest.py 通过 MATHBANK_SOURCE_MAP
指向仓库根的 source_canonical_map.example.json（占位「第一中学」），
测试不依赖被 .gitignore 的 data/source_canonical_map.json。
"""
import pytest

from mathbank.source_normalize import normalize_source, CANONICAL_MAP, UNKNOWN


@pytest.mark.parametrize("raw,expected", [
    ("", UNKNOWN),
    (None, UNKNOWN),
    ("   ", UNKNOWN),
    # 高考裸写式 -> 补 · 并入标准卷种
    ("2023全国甲卷高考真题", "2023·全国甲卷·高考真题"),
    ("2022新高考全国I卷高考真题", "2022·新高考全国Ⅰ卷·高考真题"),
    ("2023北京高考真题", "2023·北京·高考真题"),
    # 高考已规范式 -> 保持
    ("2023·全国甲卷·高考真题", "2023·全国甲卷·高考真题"),
    ("2024·广东江苏·高考真题", "2024·广东江苏·高考真题"),
    # 校内原始文件名 -> 结构化
    ("第一中学2025-2026学年高一上学期1月期末测试数学试题(1)", "高一上 · 1月期末 · 第一中学 · 2025-2026学年"),
    ("高一上一中10月月考2025-2026学年", "高一上 · 10月月考 · 第一中学 · 2025-2026学年"),
    ("2024-2025学年第一中学高一（上）月考数学试卷（10月份）", "高一上 · 10月月考 · 第一中学 · 2024-2025学年"),
    ("2023-2024学年第一中学高一（下）入学数学试卷（理科）", "高一下 · 入学考 · 第一中学 · 2023-2024学年"),
    # 专题汇编
    ("2021-2025五年高考数学真题分类汇编  专题06 导数及其应用（解答题）8种常见考法归类（全国通用）（解析版）", "高考 · 专题汇编 · 导数及其应用"),
    # 全新输入（Tier2 结构兜底：仅去噪声 + 归一分隔符，无法推断结构）
    ("2026-2027学年第一中学高一（上）期末数学试题(1)", "2026-2027学年第一中学高一（上）期末"),
])
def test_normalize_source_known_and_new(raw, expected):
    assert normalize_source(raw) == expected


def test_latex_source_with_fallback_normalizes_paper_title():
    """来源是 LaTeX 幻觉时，回退到试卷标题，且回退值仍走完整归一流程。"""
    out = normalize_source(
        r"\begin{tikzpicture}[scale=0.8]",
        fallback_title="第一中学2025-2026学年高一上学期1月期末测试数学试题(1)",
    )
    assert out == "高一上 · 1月期末 · 第一中学 · 2025-2026学年"


def test_latex_source_without_fallback_returns_unknown():
    """来源是 LaTeX 且未提供回退标题时，落到『未知』而非存储垃圾。"""
    assert normalize_source(r"\begin{tikzpicture}[scale=0.8]") == UNKNOWN
    assert normalize_source(r"\frac{x}{y}") == UNKNOWN
    assert normalize_source(r"\sqrt[3]{x} + \text{解}") == UNKNOWN


def test_legitimate_source_unaffected_by_latex_guard():
    """真实来源（无 LaTeX 特征）行为不变。"""
    assert normalize_source("2023·全国甲卷·高考真题") == "2023·全国甲卷·高考真题"
    assert normalize_source("第一中学高2025届高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"


def test_zhou_lian_zhongduan_alias_canonicalized():
    # 上期期末 + 下期阶段性 两个长期遗留的原始串 → 标准模板
    assert normalize_source('第一中学高2025级高一上期期末测试') == '高一上 · 1月期末 · 第一中学 · 2025-2026学年'
    assert normalize_source('第一中学高2025级高一下期阶段性测试') == '高一下 · 阶段性测试 · 第一中学 · 2025-2026学年'


def test_canonical_map_is_closed_and_idempotent():
    """每个规约终态自身也是键，且归一后不变（脚本重跑与钩子幂等的保证）。"""
    for value in list(CANONICAL_MAP.values()):
        assert value in CANONICAL_MAP, f"规约终态未作为键闭合: {value!r}"
        assert normalize_source(value) == value


def test_raw_forms_all_map_to_canonical():
    """原 67 种原始写法经归一后，应全部落入受控的规约终态集合（无残留裸写）。"""
    raw_forms = [
        "2024全国甲卷高考真题", "2021全国甲卷高考真题", "2023北京高考真题",
        "2023全国甲卷高考真题", "2023全国乙卷高考真题", "2022新高考全国I卷高考真题",
        "2022全国甲卷高考真题",
        "2021-2025五年高考数学真题分类汇编  专题06 导数及其应用（解答题）8种常见考法归类（全国通用）（解析版）",
        "2021-2025五年高考数学真题分类汇编  专题12 数列（解答题）9种常见考法归类（全国通用）（解析版）",
    ]
    expected_finals = {
        "2024·全国甲卷·高考真题", "2021·全国甲卷·高考真题", "2023·北京·高考真题",
        "2023·全国甲卷·高考真题", "2023·全国乙卷·高考真题", "2022·新高考全国Ⅰ卷·高考真题",
        "2022·全国甲卷·高考真题", "高考 · 专题汇编 · 导数及其应用", "高考 · 专题汇编 · 数列",
    }
    got = {normalize_source(r) for r in raw_forms}
    assert got == expected_finals


# ---------------------------------------------------------------------------
# Tier 1.5：通用「周练」裸写式（学校 + 高N级/届 + 学段 + 可选第N周 + 练）
# ---------------------------------------------------------------------------

def test_zhoulian_keeps_week_number_when_present():
    """有『第 N 周』时保留进考试类型段，避免第三周 / 第四周塌缩成同一来源。"""
    assert normalize_source("第一中学高2028届高一上第三周练") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"
    assert normalize_source("第一中学高2028届高一上第四周练") == "高一上 · 第四周练 · 第一中学 · 2025-2026学年"
    # 数字周次同样支持
    assert normalize_source("第一中学高2028届高一上第12周练") == "高一上 · 第12周练 · 第一中学 · 2025-2026学年"


def test_zhoulian_without_week_number_degrades_gracefully():
    """没有周次时退化为裸『周练』，不影响无周次来源的归一。"""
    assert normalize_source("第一中学高2028届高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"
    # 库内既有条目（无周次）行为不变
    assert normalize_source("第一中学高2025届高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"


def test_zhoulian_distinguishes_ji_and_jie():
    """『级』= 入学年份正推，『届』= 毕业年份倒推，两者语义不同。"""
    # 级：入学 2025 → 高一 2025-2026 / 高三 2027-2028
    assert normalize_source("第一中学高2025级高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"
    assert normalize_source("第一中学高2025级高三上周练") == "高三上 · 周练 · 第一中学 · 2027-2028学年"
    # 届：毕业 2028 → 高一 2025-2026 / 高二 2026-2027 / 高三 2027-2028
    assert normalize_source("第一中学高2028届高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"
    assert normalize_source("第一中学高2028届高二上周练") == "高二上 · 周练 · 第一中学 · 2026-2027学年"
    assert normalize_source("第一中学高2028届高三上周练") == "高三上 · 周练 · 第一中学 · 2027-2028学年"


def test_zhoulian_does_not_misclassify_other_exam_types():
    """非『周练』结尾的考试类型不得被通用规则误伤（仍走 Tier2 兜底）。"""
    assert normalize_source("第一中学高2028届高一上10月月考") == "第一中学高2028届高一上10月月考"
    assert normalize_source("第一中学高2025级高一上期期末测试") == "高一上 · 1月期末 · 第一中学 · 2025-2026学年"
    assert normalize_source("2023·全国甲卷·高考真题") == "2023·全国甲卷·高考真题"


def test_zhoulian_tolerates_trailing_noise_and_spaces():
    """尾缀噪声（数学试题）与多余空格不应阻断结构识别。"""
    assert normalize_source("第一中学高2028届高一上第三周练数学试题") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"
    assert normalize_source("第一中学 高2028届 高一上 第三周练") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"
    # 学校名含多校区 / 别名时正常提取（「一中」简称 → 「第一中学」全称）
    assert normalize_source("一中高2028届高一上周练") == "高一上 · 周练 · 第一中学 · 2025-2026学年"


def test_zhoulian_alias_does_not_double_expand_canonical():
    """别名是规范名子串时，不得对已规范的学校名二次展开（根因回归）。"""
    assert normalize_source("第一中学高2028届高一上第三周练") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"
    assert normalize_source("高一上第一中学10月月考2025-2026学年") == "高一上第一中学10月月考2025-2026学年"


def test_zhoulian_canonical_values_are_idempotent():
    """通用规则产出的规约终态必须幂等（脚本重跑 & 实时钩子重复调用安全）。"""
    for value in [
        "高一上 · 第三周练 · 第一中学 · 2025-2026学年",
        "高一上 · 周练 · 第一中学 · 2025-2026学年",
        "高二上 · 周练 · 第一中学 · 2026-2027学年",
        "高三上 · 周练 · 第一中学 · 2027-2028学年",
    ]:
        assert normalize_source(value) == value


# ---------------------------------------------------------------------------
# 新增规约：模拟 / 联考 / 教辅 与 分隔符幂等（2026-09-10 规约扩展）
# ---------------------------------------------------------------------------

def test_mock_exam_and_liankao_normalized():
    """模拟 / 联考此前无规约，只能以 2 段裸写式入库；现自动贴合规约。"""
    assert normalize_source("2025 · 江苏盐城模拟") == "2025 · 江苏盐城 · 模拟"
    assert normalize_source("2025 · 江苏模拟") == "2025 · 江苏 · 模拟"
    assert normalize_source("2025 · T8联考") == "2025 · T8 · 联考"
    assert normalize_source("2024 · 九省联考") == "2024 · 九省 · 联考"
    assert normalize_source("2024 · 全国模拟") == "2024 · 全国 · 模拟"


def test_mock_exam_output_is_idempotent():
    """本层产出的规约终态必须幂等（实时钩子会重复调用）。"""
    for value in ["2025 · 江苏盐城 · 模拟", "2025 · T8 · 联考", "2024 · 九省 · 联考"]:
        assert normalize_source(value) == value


def test_zhoulian_tolerates_repeated_zhou_character():
    """真实文件名存在「第三周周练」（周字重复）写法，规则须一并覆盖。"""
    assert normalize_source("第一中学高2028届高一上第三周周练") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"
    # 既有的单「周」写法行为不变
    assert normalize_source("第一中学高2028届高一上第三周练") == "高一上 · 第三周练 · 第一中学 · 2025-2026学年"


def test_gaokao_both_separator_forms_are_idempotent():
    """高考真题的两种分隔符均合规，归一时不互改（否则同卷会裂成两条来源）。"""
    from mathbank.source_normalize import is_canonical_source
    for value in ["2023·全国甲卷·高考真题", "2023 · 全国甲卷 · 高考真题"]:
        assert is_canonical_source(value), f"应判合规: {value!r}"
        assert normalize_source(value) == value


def test_is_canonical_source_covers_all_six_rules():
    """六类规约各取一例判合规，游离写法判不合规。"""
    from mathbank.source_normalize import is_canonical_source
    canonical = [
        "高一上 · 12月月考 · 成都七中 · 2023-2024学年",
        "2023·全国甲卷·高考真题",
        "2025 · 江苏盐城 · 模拟",
        "2026 · 四川高三第一次教学质量联合测评 · 联考",
        "高考 · 专题汇编 · 数列",
        "教辅 · 一数必刷100题",
        "未知",
        # 空值归一后即「未知」，故同样不计为违规残留
        "", None,
    ]
    for v in canonical:
        assert is_canonical_source(v), f"应判合规: {v!r}"

    non_canonical = [
        "2023-2024学年四川省成都七中高一（上）月考",  # 原始文件名
        "2025 · 江苏盐城模拟",                        # 2 段裸写式
        "高一集合运算小测",                            # 教辅未加前缀
        "2024 · 全国甲卷",                             # 高考漏后缀
    ]
    for v in non_canonical:
        assert not is_canonical_source(v), f"应判不合规: {v!r}"


def test_all_db_sources_are_canonical():
    """库内题源必须全部符合规约（回归防线：归一化脚本改动 / 新来源入库后自查）。

    仅当数据库文件存在时执行，避免在无 DB 的 CI 环境误报失败。
    """
    import sqlite3
    from pathlib import Path

    from mathbank.source_normalize import is_canonical_source

    db = Path(__file__).resolve().parent.parent / "math_question_bank.db"
    if not db.is_file():
        pytest.skip("本地题库不存在，跳过库内合规校验")

    con = sqlite3.connect(str(db))
    try:
        sources = [r[0] for r in con.execute("SELECT DISTINCT source FROM questions")]
    finally:
        con.close()

    offenders = [s for s in sources if not is_canonical_source(s)]
    assert not offenders, f"库内存在不符合规约的题源: {offenders}"
