# -*- coding: utf-8 -*-
"""错题识别：教材定位自动落库（学段/章节/小节）+ TikZ 零 token 兜底。

用户 2026-09-18 在审校页发现两件事，这个文件把修好的两段钉住：

1. **识别不出学段**。根因不是模型偷懒，而是错题块提示词里明文写着
   「不判定教材章节、学段或知识点来源」，且 `_apply_mistake_payload` 压根没有
   `category_*` 的写入分支。那条禁令的前提是「物化没有教材树、一期不进题库」，
   而这两条现在都作废了（教科版物理 / 人教版化学两棵树随三科错题库一起落地，
   物化照样入库）—— 于是每道物化错题入库后都掉进「未分类」桶。
   现在提示词照学科传树、写回时走后端既有的受控归一。

2. **模型自绘 TikZ**。产品口径是错题流程不做 TikZ 编译、图由人工截图补入，
   可模型偶发无视规则（实测同一批 4 道题里 2 道自行画了整段 tikzpicture）。
   那段代码前端只能当纯文本显示，落到审校页就是一片读不懂的红字。
   现在后端做零 token 兜底：换算成 `[插图待补: 图N]` 占位符芯片。

断言分四组：提示词（传树/不传树）、写回（三级落库、只填空值、拒绝自造名）、
TikZ 兜底（成对/单边/无 TikZ）、接线（两处调用点必须真的传了树）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from mathbank.database import MistakeRecord

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRICULUMS = PROJECT_ROOT / "mathbank" / "resources" / "curriculums"


@lru_cache(maxsize=None)
def _curriculum(code: str) -> dict:
    return json.loads((CURRICULUMS / f"{code}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _main():
    import main

    return main


@lru_cache(maxsize=1)
def _prompts():
    """延迟导入 prompts：改动前的版本里没有这些构建函数，导入期报错会让整个
    模块收集失败，反向对照就只能看到一条 collection error，看不出到底钉住了哪几条。"""

    import mathbank.prompts as prompts

    return prompts


# 题干里的一小段：这张卷子的第 6 题（v-t 图像），模型当时自己画了图
TIKZ_BLOCK = (
    "\\begin{tikzpicture}[scale=0.8]\n"
    "\\draw[->] (-0.2,0) -- (4.5,0) node[right] {$t/\\text{s}$};\n"
    "\\draw[->] (0,-1.2) -- (0,2.2) node[above] {$v/(\\text{m}\\cdot\\text{s}^{-1})$};\n"
    "\\draw[thick] (0,0) -- (1,1) -- (2,2) node[above left] {A};\n"
    "\\end{tikzpicture}"
)

CHOICES_BLOCK = (
    "\\begin{choices}\n"
    "\\item 质点A的加速度大小为$0.5\\text{ m/s}^2$\n"
    "\\item $t=1\\text{ s}$时，质点B的运动方向发生改变\n"
    "\\end{choices}"
)


# ------------------------------------------------------- 1) 提示词：传树 / 不传树


def test_curriculum_text_lists_three_levels_with_sections() -> None:
    """错题版教材范围必须列到**小节**：只给到章节，模型只能在标签里自由发挥。"""

    text = _prompts().build_mistake_curriculum_text(_curriculum("PJK"))
    assert "- 必修一" in text
    assert "2. 匀变速直线运动的规律" in text
    assert "2.2 匀变速直线运动速度与时间的关系" in text


def test_prompt_carries_the_subject_curriculum() -> None:
    prompt = _prompts().build_mistake_block_system_prompt("physics", _curriculum("PJK"))
    assert "【学段 compulsory 受控取值】: 必修一、必修二" in prompt
    # 三级名称都要求逐字照抄
    assert '"compulsory"' in prompt
    assert '"chapter"' in prompt
    assert '"category_knowledge"' in prompt
    assert "逐字" in prompt


def test_prompt_drops_the_old_no_classification_rule_when_tree_passed() -> None:
    """传了树就不能再写「不判定教材章节、学段」—— 那是自相矛盾。"""

    prompt = _prompts().build_mistake_block_system_prompt("chemistry", _curriculum("CRJ"))
    assert "不判定教材章节、学段" not in prompt
    assert "顺带做教材定位" in prompt


def test_prompt_without_tree_keeps_the_conservative_fallback() -> None:
    """拿不到树时宁可不判：绝不能让模型在没有受控列表的情况下自造章节名。"""

    prompt = _prompts().build_mistake_block_system_prompt("math")
    assert "不判定教材章节、学段或知识点来源。" in prompt
    assert '"category_knowledge"' not in prompt
    assert '"compulsory"' not in prompt


def test_prompt_forbids_drawing_with_a_single_positive_rule() -> None:
    """图形规则要「留位」在前、「别画」在后。

    这个项目栽过一次：同一句里「严禁 X」与「必须 X」并列会互相稀释
    （选择题选项被整组剥离就是这么来的），所以规则 4 必须先给唯一动作。
    """

    prompt = _prompts().build_mistake_block_system_prompt("physics", _curriculum("PJK"))
    rule4 = prompt.index("4. 题块里的几何图")
    head = prompt[rule4:rule4 + 300]
    assert "留位不画" in prompt
    # 「只写一个占位标记」是规则 4 里唯一的动作要求，禁写清单排在它后面
    assert head.index("只写一个占位标记") < head.index("不要包任何 LaTeX 环境")
    assert prompt.count("tikzpicture") >= 2, "只在规则 4 提一次，模型容易当耳旁风"
    # 真机实测：模型被禁止画 TikZ 之后改用 includegraphics 占位，所以这条也要点名
    assert "includegraphics" in head


# ------------------------------------------------- 2) 写回：三级落库 / 只填空值


def test_category_fills_all_three_levels_from_verbatim_payload() -> None:
    """模型逐字给出三级时三档全落，且落的正是教材树里的原字符串。"""

    main = _main()
    record = MistakeRecord()
    main._apply_mistake_category(
        record,
        {
            "compulsory": "必修一",
            "chapter": "2. 匀变速直线运动的规律",
            "category_knowledge": "2.2 匀变速直线运动速度与时间的关系",
        },
        _curriculum("PJK"),
    )
    assert record.category_compulsory == "必修一"
    assert record.category_chapter == "2. 匀变速直线运动的规律"
    assert record.category_knowledge == "2.2 匀变速直线运动速度与时间的关系"


def test_category_is_inferred_from_knowledge_tags_when_chapter_is_missing() -> None:
    """模型只报了学段时，章节/小节仍可由知识点标签反推（唯一胜出才填）。"""

    main = _main()
    record = MistakeRecord()
    main._apply_mistake_category(
        record,
        {"compulsory": "必修一", "knowledge_tags": ["2.2 匀变速直线运动速度与时间的关系"]},
        _curriculum("PJK"),
    )
    assert record.category_compulsory == "必修一"
    assert record.category_chapter, "章节该从知识点反推出来"
    assert record.category_knowledge == "2.2 匀变速直线运动速度与时间的关系"


def test_category_keeps_the_manual_pick_and_skips_the_rest() -> None:
    """人工选过学段、且与模型判断冲突时，章节/小节也不许填。

    只捂学段、照填章节的话会凑出「必修二 + 必修一的某章」这种跨书组合，
    下拉里会冒出一个该书根本没有的章节。
    """

    main = _main()
    record = MistakeRecord(category_compulsory="必修二")
    main._apply_mistake_category(
        record,
        {
            "compulsory": "必修一",
            "chapter": "2. 匀变速直线运动的规律",
            "category_knowledge": "2.2 匀变速直线运动速度与时间的关系",
        },
        _curriculum("PJK"),
    )
    assert record.category_compulsory == "必修二"
    assert not record.category_chapter
    assert not record.category_knowledge


def test_category_still_fills_chapter_when_manual_pick_agrees() -> None:
    """人工只选了学段、且与模型一致时，章节/小节该补上（不是整组放弃）。"""

    main = _main()
    record = MistakeRecord(category_compulsory="必修一")
    main._apply_mistake_category(
        record,
        {"compulsory": "必修一", "chapter": "2. 匀变速直线运动的规律"},
        _curriculum("PJK"),
    )
    assert record.category_compulsory == "必修一"
    assert record.category_chapter == "2. 匀变速直线运动的规律"


def test_category_never_writes_invented_names() -> None:
    """自造书名 / 教辅专题名一律不落库 —— 落了下拉里就会多出一个树上没有的选项。"""

    main = _main()
    record = MistakeRecord()
    main._apply_mistake_category(
        record,
        {
            "compulsory": "选择性必修第一册",
            "chapter": "专题三 运动学综合",
            "category_knowledge": "微元法求速度",
        },
        _curriculum("PJK"),
    )
    assert not record.category_compulsory
    assert not record.category_chapter
    assert not record.category_knowledge


def test_category_is_a_noop_without_a_tree() -> None:
    main = _main()
    record = MistakeRecord()
    main._apply_mistake_category(
        record, {"compulsory": "必修一", "chapter": "2. 匀变速直线运动的规律"}, None
    )
    assert not record.category_compulsory
    assert not record.category_chapter


def test_apply_payload_writes_content_and_category_together() -> None:
    """完整写回一遍：题面归一 + 分类落库，两者互不干扰。"""

    main = _main()
    record = MistakeRecord()
    main._apply_mistake_payload(
        record,
        {
            "question_no": "6",
            "content": "（多选）两质点A、B从同一地点开始运动的速度—时间图像如图所示。",
            "question_type": "multi_choice",
            "compulsory": "必修一",
            "chapter": "1. 描述运动的基本概念",
            "knowledge_tags": ["速度时间图象"],
        },
        _curriculum("PJK"),
    )
    assert record.question_no == "6"
    assert record.question_type == "multi_choice"
    assert record.category_compulsory == "必修一"
    assert record.category_chapter == "1. 描述运动的基本概念"


# --------------------------------------------------------- 3) TikZ → 占位符兜底


def test_tikz_block_becomes_a_placeholder_chip() -> None:
    main = _main()
    content = "（多选）两质点A、B的速度—时间图像如图所示，下列说法正确的是\n" + TIKZ_BLOCK
    cleaned = main.replace_drawn_figures_with_placeholders(content)
    assert "tikzpicture" not in cleaned.lower()
    assert "[插图待补: 图1]" in cleaned
    assert "下列说法正确的是" in cleaned, "同段别的正文必须原样留着"


def test_tikz_placeholders_are_renumbered_in_appearance_order() -> None:
    """前端是**按占位符出现顺序**配对 figure_images 的，编号与顺序错位就配错图。"""

    main = _main()
    content = (
        "第一处\n" + TIKZ_BLOCK + "\n看图\n[插图待补: 图7]\n" + TIKZ_BLOCK
    )
    cleaned = main.replace_drawn_figures_with_placeholders(content)
    assert cleaned.count("[插图待补: 图1]") == 1
    assert cleaned.count("[插图待补: 图2]") == 1
    assert cleaned.count("[插图待补: 图3]") == 1
    assert "[插图待补: 图7]" not in cleaned
    assert cleaned.index("[插图待补: 图1]") < cleaned.index("[插图待补: 图2]") < cleaned.index("[插图待补: 图3]")


def test_dangling_tikz_tokens_are_erased_too() -> None:
    """模型只写了一半（有 \\begin 没 \\end）也不能漏 —— 那串代码一样渲染不了。"""

    main = _main()
    cleaned = main.replace_drawn_figures_with_placeholders(
        "如图所示，下列说法正确的是\n\\begin{tikzpicture}[scale=0.8]\n\\draw[->] (0,0) -- (1,1);"
    )
    assert "tikzpicture" not in cleaned.lower()
    assert "[插图待补: 图1]" in cleaned


def test_fake_center_and_includegraphics_is_unwrapped_to_the_marker() -> None:
    """真机实测的第二种写法：模型不画 TikZ 了，改用 center + includegraphics 占位。

    KaTeX 一个都不认（渲染端只支持数学环境），不拦就是一行纯文本乱码。
    这是 2026-09-18 拿 Qwen3-VL-8B 真跑 p003_b07.png 拿到的原样输出。
    """

    main = _main()
    content = (
        "（多选）两质点A、B从同一地点开始运动的速度—时间图像如图所示，下列说法正确的是\n\n"
        "\\begin{center}\n"
        "\\includegraphics[width=0.3\\textwidth]{image-placeholder} \\quad "
        "\\text{[插图待补: 图1]}\n"
        "\\end{center}\n\n"
        + CHOICES_BLOCK
    )
    cleaned = main.replace_drawn_figures_with_placeholders(content)
    assert "includegraphics" not in cleaned
    assert "\\begin{center}" not in cleaned and "\\end{center}" not in cleaned
    assert "\\quad" not in cleaned
    # 只脱掉**裹着占位符**的那个 \text；选项里的 $\text{ m/s}^2$ 是正常数学写法，必须留着
    assert "\\text{[插图待补" not in cleaned
    assert "[插图待补: 图1]" in cleaned
    assert "\\text{ m/s}^2" in cleaned
    # 占位符独占一行，前后不留模型留下的排版壳子
    lines = [line for line in cleaned.split("\n") if line.strip()]
    assert "[插图待补: 图1]" in lines
    assert "下列说法正确的是" in cleaned
    assert "\\begin{choices}" in cleaned, "选项环境不能被这段兜底连带吃掉"


def test_figure_environment_and_caption_are_unwrapped_too() -> None:
    """figure 环境 + caption + minipage 这一套同样只脱壳、不吞内容。"""

    main = _main()
    content = (
        "如图所示，下列说法正确的是\n"
        "\\begin{figure}[h]\n\\centering\n"
        "\\includegraphics[width=0.5\\textwidth]{fig.png}\n"
        "\\caption{速度—时间图像}\n[插图待补: 图1]\n"
        "\\end{figure}\n"
    )
    cleaned = main.replace_drawn_figures_with_placeholders(content)
    assert "includegraphics" not in cleaned
    assert "\\begin{figure}" not in cleaned and "\\end{figure}" not in cleaned
    assert "caption" not in cleaned
    assert "[插图待补: 图1]" in cleaned
    assert "速度—时间图像" not in cleaned, "caption 是模型编的说明文字，一并清掉"
    assert "如图所示，下列说法正确的是" in cleaned


def test_tikz_fallback_is_a_noop_without_tikz() -> None:
    """没有 TikZ 就一个字节都不许动 —— 人工写好的编号不能被重排。"""

    main = _main()
    content = "题干\n[插图待补: 图3]\n\\begin{choices}\n\\item $a>0$\n\\end{choices}"
    assert main.replace_drawn_figures_with_placeholders(content) == content


def test_apply_payload_routes_content_through_the_tikz_fallback() -> None:
    """写回必须真的走兜底：模型画了图，落库的题面里就不能再有 tikzpicture。"""

    main = _main()
    record = MistakeRecord()
    main._apply_mistake_payload(
        record,
        {"content": "两质点的速度—时间图像如图所示\n" + TIKZ_BLOCK + "\n" + CHOICES_BLOCK},
        None,
    )
    assert record.content and "tikzpicture" not in record.content.lower()
    assert "[插图待补: 图1]" in record.content
    assert "\\begin{choices}" in record.content, "选项环境不能被这一段兜底连带吃掉"


# ------------------------------------------------------------------- 4) 接线


def test_recognize_passes_the_subject_tree_and_the_task_passes_it_on() -> None:
    """两个调用点都得接上，少一处就表现成「改了没效果」。

    ① `recognize_mistake_block` 必须把**该学科的**树传进提示词；
    ② 识别任务必须把树传给 `_apply_mistake_payload`，否则模型答了也写不进去。
    """

    source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert (
        "prompt = build_mistake_block_system_prompt(\n"
        "        subject, get_subject_curriculum_tree(subject)\n"
        "    )"
    ) in source
    assert "_apply_mistake_payload(record, payload, curriculum)" in source
    assert 'curriculum = get_subject_curriculum_tree(subject)' in source


def test_ocr_cleanup_also_sanitizes_tikz() -> None:
    """解析截图识别走的是 POST /api/ocr，那条链路也得拦一道。"""

    source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    cleanup_at = source.index('re.sub(r"\\[ILLUSTRATION_BOX')
    assert "replace_drawn_figures_with_placeholders(latex_content)" in source[cleanup_at:cleanup_at + 600]
