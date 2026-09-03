"""题目来源（questions.source）归一化 —— 单一事实源（single source of truth）。

本模块同时服务于两条路径，保证「事后归一」与「实时录入归一」语义完全一致：

1. 一次性批量归一脚本 ``scripts/normalize_sources.py``（直接复用本模块的 ``CANONICAL_MAP``）。
2. 所有写库入口的实时钩子（手动保存 / AI·OCR 导入 / PDF·DOCX 拆卷），在落库前调用
   ``normalize_source()``，使今后新录入的来源自动贴合规约，杜绝再次混乱。

命名规约
--------
- 校内考试 : ``{学段} · {考试类型} · {学校} · {学年}``
- 高考真题 : ``{年份} · {卷种} · 高考真题``
- 专题汇编 : ``高考 · 专题汇编 · {专题}``
- 空值     : ``未知``

归一分两层（与 latex_normalize 的「双保险」思路一致）：

- **Tier 1 — 精确别名表（CANONICAL_MAP）**：覆盖库内已出现过的全部取值，及其原始
  文件名 / 裸写形式，直接返回规范写法。这是最可靠的一层。
- **Tier 2 — 结构兜底（best-effort）**：针对全新输入，剥离文件名噪声尾缀、归一分隔符
  为 `` · ``、归一卷种罗马数字（I/II/III → Ⅰ/Ⅱ/Ⅲ）、应用用户配置的学校别名（见下文）。
  全新来源无法被自动推断结构，仅做噪声清理；若仍想规整，可手动录入规范写法或扩展本表。

个人化 Tier 1 映射与学校别名（按学校 / 地区定制）
--------------------------------------------------
 本校相关条目（含 identity 与裸写 / 原始 PDF 文件名形式）**不写在本文件**，
 统一从运行时配置文件加载，搜索顺序如下（首个存在且合法者胜出）：

 1. ``<项目根>/data/source_canonical_map.json``（个人题库实例配置；已被 .gitignore 忽略）
 2. ``~/.config/mathbank/source_canonical_map.json``（跨项目用户配置，可选）

 加载失败（缺失 / JSON 语法错误 / 字段非字典）会**静默回退**到本模块内置的通用
 Tier 1（高考真题 + 专题汇编 + 空值），不会阻断入库。文件结构示例见
 ``source_canonical_map.example.json``（仓库根目录）。

 学校别名亦来自用户配置文件的 ``school_aliases`` 字段，与 Tier 1.5 周练结构识别
 共享同一别名表。
"""

import json
import logging
import os
import re
from pathlib import Path

# 空值兜底
UNKNOWN = "未知"

# LaTeX / TikZ 片段判定：真实来源是「中文 + 分隔符」的可读字符串，绝不含反斜杠命令。
# 模型在拿不准 source 时，可能退化输出一段图骨架（如 `\begin{tikzpicture}[scale=0.8]`）
# 或数学命令塞进 source 字段；命中以下任一特征即视为无效来源（幻觉）。
_LATEX_HINT_RE = re.compile(
    r"\\(begin|end)\s*\{"                                  # \begin{ / \end{
    r"|\\(frac|sqrt|text|mathrm|mathbb|mathbf|includegraphics|tikzpicture"
    r"|overline|overrightarrow|operatorname|left|right|cdot|times|alpha"
    r"|beta|theta|sum|int|lim|vec|hat|tilde|angle)\b"
    r"|\\\w+\s*[\{\[]"                                    # 任意 \command{ 或 \command[
)


def _looks_like_latex(s: str) -> bool:
    """判断字符串是否像 LaTeX/TikZ 片段（而非正常来源文本）。"""
    if not s:
        return False
    return bool(_LATEX_HINT_RE.search(s))

# 文件名噪声尾缀（Tier 2 剥离；Tier 1 已含带噪声的原始文件名精确映射）
# ⚠️ 只保留「具体、几乎不会出现在真实标题中」的尾缀；泛化的「试卷」「试题」已移除，
# 否则会误伤真实标题（如「公式保真测试卷」末二字「试卷」被整段截掉）。
NOISE_TOKENS = [
    "数学试题", "数学试卷",
    "（原卷版）", "(原卷版)", "（解析版）", "(解析版)",
    "（全国通用）", "(全国通用)",
    "（理科）", "(理科)", "（文科）", "(文科)",
    "解析版", "原卷版",
    "（1）", "(1)", "（2）", "(2)", "（3）", "(3)",
    "（5月份）", "(5月份)", "（10月份）", "(10月份)",
    "（六）", "(六)",
]

# 学校别名（运行时加载，初始为空；详见模块底部 _load_user_canonical_map）
SCHOOL_ALIASES: dict[str, str] = {}

# Tier 1 — 内置通用规则（与具体学校/地区无关）。
# 仅含高考真题 identity / 裸写式归一 / 专题汇编 / 空值；个人题库本校相关条目
# 请放 ``data/source_canonical_map.json``（详见模块 docstring）。
_BASE_CANONICAL_MAP: dict[str, str] = {
    # ---- 高考真题：已规范式（identity）----
    '2023·全国甲卷·高考真题': '2023·全国甲卷·高考真题',
    '2023·全国乙卷·高考真题': '2023·全国乙卷·高考真题',
    '2022·全国甲卷·高考真题': '2022·全国甲卷·高考真题',
    '2022·全国乙卷·高考真题': '2022·全国乙卷·高考真题',
    '2021·全国乙卷·高考真题': '2021·全国乙卷·高考真题',
    '2024·北京·高考真题': '2024·北京·高考真题',
    '2024·全国甲卷·高考真题': '2024·全国甲卷·高考真题',
    '2021·全国甲卷·高考真题': '2021·全国甲卷·高考真题',
    '2025·北京·高考真题': '2025·北京·高考真题',
    '2022·新高考全国Ⅱ卷·高考真题': '2022·新高考全国Ⅱ卷·高考真题',
    '2021·北京·高考真题': '2021·北京·高考真题',
    '2023·北京·高考真题': '2023·北京·高考真题',
    '2022·北京·高考真题': '2022·北京·高考真题',
    '2025·上海·高考真题': '2025·上海·高考真题',
    '2022·浙江·高考真题': '2022·浙江·高考真题',
    '2023·新课标Ⅱ卷·高考真题': '2023·新课标Ⅱ卷·高考真题',
    '2023·新课标Ⅰ卷·高考真题': '2023·新课标Ⅰ卷·高考真题',
    '2023·天津·高考真题': '2023·天津·高考真题',
    '2025·天津·高考真题': '2025·天津·高考真题',
    '2021·上海·高考真题': '2021·上海·高考真题',
    '2024·上海·高考真题': '2024·上海·高考真题',
    '2022·上海·高考真题': '2022·上海·高考真题',
    '2021·浙江·高考真题': '2021·浙江·高考真题',
    '2025·全国二卷·高考真题': '2025·全国二卷·高考真题',
    '2025·全国一卷·高考真题': '2025·全国一卷·高考真题',
    '2024·天津·高考真题': '2024·天津·高考真题',
    '2022·新高考全国Ⅰ卷·高考真题': '2022·新高考全国Ⅰ卷·高考真题',
    '2022·天津·高考真题': '2022·天津·高考真题',
    '2021·新高考全国Ⅰ卷·高考真题': '2021·新高考全国Ⅰ卷·高考真题',
    '2024·新课标Ⅰ卷·高考真题': '2024·新课标Ⅰ卷·高考真题',
    '2024·新课标Ⅱ卷·高考真题': '2024·新课标Ⅱ卷·高考真题',
    '2023·上海·高考真题': '2023·上海·高考真题',
    '2021·新高考全国Ⅱ卷·高考真题': '2021·新高考全国Ⅱ卷·高考真题',
    '2021·天津·高考真题': '2021·天津·高考真题',
    '2024·广东江苏·高考真题': '2024·广东江苏·高考真题',
    # ---- 高考真题：裸写式 -> 补 · 并入标准卷种 ----
    '2024全国甲卷高考真题': '2024·全国甲卷·高考真题',
    '2021全国甲卷高考真题': '2021·全国甲卷·高考真题',
    '2023北京高考真题': '2023·北京·高考真题',
    '2023全国甲卷高考真题': '2023·全国甲卷·高考真题',
    '2023全国乙卷高考真题': '2023·全国乙卷·高考真题',
    '2022新高考全国I卷高考真题': '2022·新高考全国Ⅰ卷·高考真题',
    '2022全国甲卷高考真题': '2022·全国甲卷·高考真题',
    # ---- 专题汇编 -> 专题 ----
    '2021-2025五年高考数学真题分类汇编  专题06 导数及其应用（解答题）8种常见考法归类（全国通用）（解析版）': '高考 · 专题汇编 · 导数及其应用',
    '2021-2025五年高考数学真题分类汇编  专题12 数列（解答题）9种常见考法归类（全国通用）（解析版）': '高考 · 专题汇编 · 数列',
    # ---- 空值 ----
    '': '未知',
}

# 闭合基础映射：每个规范终态也作为自身键，保证已归一的值幂等通过
for _v in list(_BASE_CANONICAL_MAP.values()):
    _BASE_CANONICAL_MAP.setdefault(_v, _v)


# ---------------------------------------------------------------------------
# 用户配置文件加载（Tier 1 + 学校别名）
# ---------------------------------------------------------------------------
_USER_MAP_FILENAME = "source_canonical_map.json"
_USER_MAP_SEARCH_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / _USER_MAP_FILENAME,
    Path.home() / ".config" / "mathbank" / _USER_MAP_FILENAME,
]


def _resolve_user_map_paths() -> list[Path]:
    """返回用户配置文件的搜索路径；支持环境变量覆盖。

    ``MATHBANK_SOURCE_MAP`` 指向一个替代配置文件时优先使用（测试/CI 用它指向
    仓库根的 ``source_canonical_map.example.json``，避免依赖被 .gitignore 的
    个人化 ``data/source_canonical_map.json``）。
    """
    override = os.environ.get("MATHBANK_SOURCE_MAP")
    if override:
        return [Path(override)]
    return _USER_MAP_SEARCH_PATHS


def _load_user_canonical_map() -> dict[str, dict]:
    """从用户配置文件加载个人化 Tier 1 映射与学校别名。

    返回 ``{"entries": {...}, "aliases": {...}}``。
    加载失败（缺失 / JSON 语法错 / 字段非字典）返回空 dict 对应项，不抛异常。
    """
    for path in _resolve_user_map_paths():
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("source_normalize: 加载用户映射失败 %s (%s)", path, exc)
            return {"entries": {}, "aliases": {}}
        if not isinstance(data, dict):
            logging.warning("source_normalize: 用户映射根非字典 %s", path)
            return {"entries": {}, "aliases": {}}

        entries_raw = data.get("school_entries", {})
        aliases_raw = data.get("school_aliases", {})
        if not isinstance(entries_raw, dict) or not isinstance(aliases_raw, dict):
            logging.warning("source_normalize: school_entries/school_aliases 非字典 %s", path)
            return {"entries": {}, "aliases": {}}

        # 过滤掉说明字段：以纯虚线或下划线开头（如 "----- 校内：…-----": ""、
        # "_doc": "..."）。真实映射条目 key 为可读中文/数字。
        entries = {
            k: v for k, v in entries_raw.items()
            if isinstance(k, str) and isinstance(v, str)
            and not k.startswith("-----") and not k.startswith("_")
        }
        aliases = {
            k: v for k, v in aliases_raw.items()
            if isinstance(k, str) and isinstance(v, str)
            and not k.startswith("-----") and not k.startswith("_")
        }
        return {"entries": entries, "aliases": aliases}
    return {"entries": {}, "aliases": {}}


_USER_MAP = _load_user_canonical_map()

# 公开 CANONICAL_MAP：基础 + 用户映射（用户条目覆盖基础同键值）
CANONICAL_MAP: dict[str, str] = dict(_BASE_CANONICAL_MAP)
CANONICAL_MAP.update(_USER_MAP["entries"])

# 闭合最终映射：每个规范终态也作为自身键
for _v in list(CANONICAL_MAP.values()):
    CANONICAL_MAP.setdefault(_v, _v)

# 公开 SCHOOL_ALIASES：运行时副本（基础为空，全部来自用户配置）
SCHOOL_ALIASES.update(_USER_MAP["aliases"])


def _strip_noise(s: str) -> str:
    for tok in NOISE_TOKENS:
        s = s.replace(tok, "")
    return s


def _apply_aliases(s: str) -> str:
    if not SCHOOL_ALIASES:
        return s
    # 先保护已存在的规范名（canonical），再展开别名，最后还原 —— 避免
    # 「一中 → 第一中学」这类子串别名把「第一中学」二次替换成「第第一中学学」。
    placeholders: dict[str, str] = {}
    for i, canonical in enumerate(dict.fromkeys(SCHOOL_ALIASES.values())):
        tok = f"\x00{i}\x00"
        placeholders[tok] = canonical
        s = s.replace(canonical, tok)
    for alias, canonical in SCHOOL_ALIASES.items():
        s = s.replace(alias, canonical)
    for tok, canonical in placeholders.items():
        s = s.replace(tok, canonical)
    return s


def _normalize_separators(s: str) -> str:
    # 统一分隔符变体为全角中点 '·'
    s = s.replace("・", "·").replace("•", "·").replace("|", "·")
    # 卷种罗马数字：全国I卷 / 新课标I卷 / 新高考I卷 -> Ⅰ（仅卷种语境）
    s = re.sub(r"(全国|新课标|新高考)I卷", r"\1Ⅰ卷", s)
    s = re.sub(r"(全国|新课标|新高考)II卷", r"\1Ⅱ卷", s)
    s = re.sub(r"(全国|新课标|新高考)III卷", r"\1Ⅲ卷", s)
    # 规范化为 ' · '（两侧空格），再折叠多余空白
    s = s.replace("·", " · ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Tier 1.5：通用「周练」裸写式结构识别
# ---------------------------------------------------------------------------
# 形如「{学校}高{YYYY}{级|届}高{一|二|三}{上|下}[第N周]练」，例：
#   第一中学高2028届高一上第三周练   ->  高一上 · 第三周练 · 第一中学 · 2025-2026学年
#   第一中学高2025级高一上周练       ->  高一上 · 周练 · 第一中学 · 2025-2026学年
#
# 学年推导（级 / 届 语义不同，必须区分）：
#   - 级 = 入学年份 N：高 L 的学年 = (N + L - 1) → (N + L)
#   - 届 = 毕业年份 N：高 L 的学年 = (N + L - 4) → (N + L - 3)（毕业年倒推）
#
# 周次「第 N」为可选：有则保留进考试类型段（便于区分第三周 / 第四周），
# 无则退化为裸「周练」，对无周次来源零影响。
_ZHOULIAN_RE = re.compile(
    r"^(?P<school>.+?)"
    r"高(?P<cohort>\d{4})(?P<kind>级|届)"
    r"高(?P<level>[一二三])(?P<term>[上下])"
    # 二选一：显式周次「第N周练」 或 裸「周练」。
    # 注意：无周次时「周」字仍存在（高一上*周*练），必须由备选分支消耗，
    # 否则整条规则只匹配带「第N」的写法。
    r"(?:第(?P<week>[一二三四五六七八九十百\d]+)周|周)"
    r"练(?:习)?$"
)

_LEVEL_NUM = {"一": 1, "二": 2, "三": 3}


def _match_zhoulian(raw: str):
    """识别「周练」类裸写式并归一为规约写法；不匹配返回 None。

    仅当结构完整（学校 + 届/级 + 学段 + 结尾『练』）时才命中，
    避免误伤其他考试类型（月考 / 期末 / 段考等）。
    """
    if not raw:
        return None
    # 该模式为纯中文 + 数字，空格无语义，先剥离以容忍「第一中学 高2028届 …」这类写法
    s = re.sub(r"\s+", "", str(raw))
    m = _ZHOULIAN_RE.match(s)
    if not m:
        return None

    school = _apply_aliases(m.group("school").strip())
    if not school:
        return None

    cohort = int(m.group("cohort"))
    kind = m.group("kind")
    level = m.group("level")
    term = m.group("term")
    week = m.group("week")

    level_num = _LEVEL_NUM[level]
    if kind == "级":
        start = cohort + level_num - 1
    else:  # 届：毕业年倒推
        start = cohort + level_num - 4

    exam_type = f"第{week}周练" if week else "周练"
    return f"高{level}{term} · {exam_type} · {school} · {start}-{start + 1}学年"


def normalize_source(raw, fallback_title=None):
    """把任意来源写法归一为规约写法。

    返回 ``UNKNOWN`` 当输入为 None / 空 / 仅空白 / 形如 LaTeX 片段（模型幻觉，
    如把图骨架 ``\\begin{tikzpicture}`` 塞进 source）；若提供 ``fallback_title``
    且其非 LaTeX / 非空，则对 fallback **递归**归一（仍走完整 Tier1/Tier2 流程），
    确保回退值也符合命名规约，而非裸文件名。

    注：``fallback_title`` 仅用于「来源无效时回退到试卷标题」，不会绕过归一。
    """
    if raw is None:
        return UNKNOWN
    s = str(raw).strip()
    if not s:
        return UNKNOWN

    # 拦截模型幻觉：来源绝不可能是 LaTeX/TikZ 片段，回退到试卷标题（再走归一）
    if _looks_like_latex(s):
        if fallback_title:
            return normalize_source(fallback_title)
        return UNKNOWN

    # Tier 1: 精确别名表
    if s in CANONICAL_MAP:
        return CANONICAL_MAP[s]

    # Tier 1.5: 通用「周练」裸写式结构识别（学校 + 届/级 + 学段 + 可选第N周 + 练）
    hit = _match_zhoulian(s)
    if hit:
        return hit

    # Tier 2: 结构兜底（best-effort）
    s2 = _strip_noise(s)
    s2 = _apply_aliases(s2)
    s2 = _normalize_separators(s2)
    s2 = re.sub(r"\s+", " ", s2).strip()

    # 兜底清理后仍可能命中精确表（如剥离尾缀后恰好是已录入的规范写法）
    if s2 in CANONICAL_MAP:
        return CANONICAL_MAP[s2]

    # Tier 2.5: 清理后重试通用规则（容忍「…第三周练数学试题」这类尾缀噪声）
    hit = _match_zhoulian(s2)
    if hit:
        return hit

    return s2 or UNKNOWN