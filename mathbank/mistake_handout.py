"""错题本 LaTeX 模板与编译入口。

与组卷导出（``mathbank.paper_helper``）的关系：**共用清理与编译能力，不共用模板**。
错题本走独立的 ``ctexart`` 版式而非组卷用的 ``exam-zh``，原因是两者的排版目标不同 ——
试卷要分值、答题栏、考试须知那一套；错题本要的是「一道题 + 一大块订正留白」，
并且要按用户的文档规范（仿宋、三号、单倍行距、首行缩进 2 字符）排版。

关于 ``choices`` / ``\\fillin`` / ``\\paren``：题干由 ``clean_content_for_latex``
清理，它产出的是 exam-zh 的宏。错题本模板自己定义了这三个名字（语义对齐、实现独立），
因此识别结果可以原样喂进来 —— 这是「不共用模板」而非「不共用内容格式」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mathbank.paper_helper import clean_content_for_latex

#: 订正留白默认高度（cm），与实施计划 §15 第 3 条一致
DEFAULT_SOLUTION_SPACE_CM = 6.0

#: 用户既有的中文文档规范：仿宋、三号
DEFAULT_FONT_SIZE = "3"

#: 可选字号（ctex 的 \zihao 取值）
FONT_SIZE_LABELS = {
    "3": "三号",
    "4": "四号",
    "-4": "小四",
    "5": "五号",
}

#: 解析来源
ANSWER_MODE_NONE = "none"
ANSWER_MODE_MANUAL = "manual"
ANSWER_MODE_AI = "ai"
#: 两种解析都收（人工截图优先，AI 生成的仍须人工核对）
ANSWER_MODE_BOTH = "both"
ANSWER_MODES = (
    ANSWER_MODE_NONE,
    ANSWER_MODE_MANUAL,
    ANSWER_MODE_AI,
    ANSWER_MODE_BOTH,
)

#: 选择题选项按列数排布：单选项文本越长，列数越少，避免溢出页面
_CHOICES_COLUMN_RULES = ((12, 4), (28, 2))


@dataclass
class HandoutOptions:
    """错题本导出选项（全部来自导出页的表单）。"""

    solution_space_cm: float = DEFAULT_SOLUTION_SPACE_CM
    include_reason: bool = True
    include_figures: bool = True
    answer_mode: str = ANSWER_MODE_NONE
    font_size: str = DEFAULT_FONT_SIZE
    show_original_number: bool = True

    def normalized(self) -> "HandoutOptions":
        mode = str(self.answer_mode or ANSWER_MODE_NONE).strip().lower()
        if mode not in ANSWER_MODES:
            mode = ANSWER_MODE_NONE
        size = str(self.font_size or DEFAULT_FONT_SIZE).strip()
        if size not in FONT_SIZE_LABELS:
            size = DEFAULT_FONT_SIZE
        try:
            space = float(self.solution_space_cm)
        except (TypeError, ValueError):
            space = DEFAULT_SOLUTION_SPACE_CM
        space = max(0.0, min(space, 22.0))
        return HandoutOptions(
            solution_space_cm=space,
            include_reason=bool(self.include_reason),
            include_figures=bool(self.include_figures),
            answer_mode=mode,
            font_size=size,
            show_original_number=bool(self.show_original_number),
        )


@dataclass
class HandoutBuildResult:
    tex_content: str
    image_paths: list[str] = field(default_factory=list)
    included_count: int = 0
    answer_count: int = 0
    #: 因「AI 生成但未人工核对」而被挡在 PDF 之外的题目（题号或序号），供导出页提示
    blocked_ai_answers: list[str] = field(default_factory=list)


# ----------------- 资源路径 -----------------


def resolve_upload_asset(web_path, static_dir: Path) -> Path | None:
    """把 ``/static/uploads/...`` 这类 Web 路径还原为磁盘路径。

    ``static_dir`` 取 ``mathbank.paths.STATIC_DIR``（测试环境会指向 test_uploads，
    路径前缀随之变化，本函数按前缀实际值解析，不做硬编码）。
    """

    text = str(web_path or "").strip()
    if not text:
        return None
    if text.startswith("/static/"):
        candidate = static_dir.parent / text.lstrip("/")
    elif text.startswith("static/"):
        candidate = static_dir.parent / text
    elif text.startswith("/uploads/"):
        candidate = static_dir / "uploads" / text[len("/uploads/"):]
    else:
        return None
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        return None
    return None


#: 正文/解析里内联的 Markdown 图片。框选补图写进正文用的就是这个语法，图片位置由这句话
#: 在文中的位置决定 —— 与题库侧组卷导出走同一套（那边 ``collect_referenced_images``
#: 也扫正文），不再出现「题面一套、题后附图区另一套」的错位。
_INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")

#: 识别阶段写下的「这里有一张图，还没补」标记。到导出这一刻还留着的，说明老师就是没打算
#: 补 —— 学生讲义上不能出现这种内部记号。
_PLACEHOLDER_MARK_RE = re.compile(r"\[插图待补\s*[:：]\s*[^\]]*\]")


def _extract_inline_images(text: str, static_dir: Path) -> tuple[str, list[str]]:
    """摘出正文内联图片，返回 (清掉失效引用后的文本, 已解析的磁盘路径)。

    失效引用必须一并拿掉：``clean_content_for_latex`` 会把它变成 ``\\includegraphics{...}``，
    而那个文件不在编译目录里 —— 一张找不到的图会让整份讲义编译失败，代价远大于少一张图。
    """

    resolved_paths: list[str] = []
    if not text:
        return text, resolved_paths

    def _replace(match: "re.Match[str]") -> str:
        resolved = resolve_upload_asset(match.group(1), static_dir)
        if resolved is None:
            return ""
        if str(resolved) not in resolved_paths:
            resolved_paths.append(str(resolved))
        return match.group(0)

    return _INLINE_IMAGE_RE.sub(_replace, text), resolved_paths


def _strip_placeholder_marks(text: str) -> str:
    """去掉没补图的占位标记，并收拢留下的空行 —— 讲义里不留内部记号，也不留空档。"""

    cleaned = _PLACEHOLDER_MARK_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


# ----------------- LaTeX 片段 -----------------


def _escape_bare_specials(text: str) -> str:
    """转义题干里语义会被 LaTeX 吞掉的两个字符。

    - ``%``：LaTeX 语义是「注释掉本行剩余内容」—— 一个漏转义的 ``%`` 会静默吞掉
      半行题干，而百分号在数学题里很常见（正确率、浓度、增长率）。
    - ``~``：LaTeX 语义是「不换行空格」，于是 ``0~4 s`` 会渲染成 ``0 4 s``，
      学生看到的区间凭空少了个连接号。OCR 输出的波浪号几乎总是表示「到」。

    刻意不碰 ``&`` 与 ``#``：``&`` 在表格里是对齐符，误转义会直接弄坏题目自带的
    表格；``#`` 在题干里罕见且转义收益低。
    """

    text = re.sub(r"(?<!\\)%", r"\\%", text or "")
    # 已是 \~ 的重音写法或有反斜杠前缀的不动
    text = re.sub(r"(?<!\\)~", r"\\textasciitilde{}", text)
    return text


def _split_choices_block(text: str) -> tuple[str, list[str]]:
    """把题干拆成 (选择题之外的题面, 选项列表)。无 choices 环境时返回 (原文, [])。"""

    match = re.search(r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}", text)
    if not match:
        return text, []
    stem = (text[: match.start()] + text[match.end():]).strip()
    inner = match.group(1)
    items: list[str] = []
    for piece in re.split(r"\\item\b", inner):
        cleaned = piece.strip()
        if cleaned:
            items.append(cleaned)
    return stem, items


def _choices_columns(items: list[str]) -> int:
    """按最长选项的可见长度挑列数，避免长选项溢出页面。"""

    if not items:
        return 1
    longest = max(len(_visible_length(item)) for item in items)
    for limit, columns in _CHOICES_COLUMN_RULES:
        if longest <= limit:
            return columns
    return 1


def _visible_length(item: str) -> str:
    """估算选项的可见宽度：去掉 LaTeX 命令与定界符，中文按 2 个字符宽计。"""

    text = re.sub(r"\\[a-zA-Z]+\*?", "", item or "")
    text = re.sub(r"[${}\\]", "", text)
    return text


def _render_choices(items: list[str]) -> list[str]:
    """把选项渲染成 tasks 环境（自动编号 A/B/C/D）。"""

    if not items:
        return []
    columns = _choices_columns(items)
    lines = [
        r"\begin{tasks}[label=\Alph*., label-width=1.6em, item-indent=1.9em,"
        rf" column-sep=1.0em, after-item-skip=2pt]({columns})"
    ]
    for item in items:
        lines.append(rf"  \task {item}")
    lines.append(r"\end{tasks}")
    return lines


def _render_figure_block(figures: list[Path]) -> list[str]:
    """图形题：人工从干净件截图后插在题干下方。"""

    if not figures:
        return []
    width = "0.62\\linewidth" if len(figures) == 1 else "0.42\\linewidth"
    lines = [r"\begin{center}"]
    for figure in figures:
        lines.append(rf"  \includegraphics[width={width}]{{{figure.name}}}")
    lines.append(r"\end{center}")
    return lines


def _render_answer_body(record: dict, static_dir: Path) -> tuple[list[str], list[str]]:
    """生成单题的解析片段，返回 (LaTeX 行, 新增图片路径)。"""

    lines: list[str] = []
    images: list[str] = []
    markdown = _strip_placeholder_marks(str(record.get("answer_markdown") or "")).strip()
    inline_images: set[str] = set()
    if markdown:
        # 解析里内联的截图同样要收进 image_paths；数组里那一份就不重复印了。
        markdown, inline_paths = _extract_inline_images(markdown, static_dir)
        markdown = markdown.strip()
        inline_images = set(inline_paths)
        for path in inline_paths:
            if path not in images:
                images.append(path)
        if markdown:
            cleaned = clean_content_for_latex(_escape_bare_specials(markdown), is_answer=True)
            lines.append(cleaned)
    for raw in record.get("answer_images") or []:
        resolved = resolve_upload_asset(raw, static_dir)
        if resolved is None:
            continue
        if str(resolved) in inline_images:
            continue
        images.append(str(resolved))
        lines.append(r"\begin{center}")
        lines.append(rf"  \includegraphics[width=0.78\linewidth]{{{resolved.name}}}")
        lines.append(r"\end{center}")
    return lines, images


# ----------------- 文档装配 -----------------


def build_mistake_handout_latex(
    *,
    title: str,
    subject: str,
    student_name: str,
    batch_date: str,
    records: list[dict],
    options: HandoutOptions,
    static_dir: Path,
) -> HandoutBuildResult:
    """把错题记录装配成一份完整的错题本 LaTeX 源码。

    ``records`` 是 ``MistakeRecord.to_dict()`` 的列表，**只读其中的快照字段** ——
    不查题库、不 join questions。题库题被改被删都不该让已出的错题本变形。
    """

    opts = options.normalized()
    subject_label = {
        "math": "数学",
        "physics": "物理",
        "chemistry": "化学",
    }.get(str(subject or "").strip().lower(), str(subject or "理科"))

    lines: list[str] = []
    image_paths: list[str] = []

    lines.append(r"% 错题本由「小陈的数学宝藏」错题工作台生成")
    lines.append(r"\documentclass[UTF8,a4paper,fontset=fandol]{ctexart}")
    lines.append(
        r"\usepackage{amsmath,amssymb,cancel,cases,mhchem,siunitx,extarrows}"
    )
    lines.append(r"\usepackage{array,booktabs,tabularx,longtable,multirow,makecell}")
    lines.append(r"\usepackage{graphicx}")
    lines.append(r"\usepackage[export]{adjustbox}")
    lines.append(r"\usepackage{geometry}")
    lines.append(r"\usepackage{tasks}")
    lines.append(r"\usepackage{enumitem}")
    lines.append(r"\usepackage{fancyhdr}")
    lines.append(r"\usepackage{xcolor}")
    lines.append(
        r"\geometry{a4paper,left=2.2cm,right=2.2cm,top=2.7cm,bottom=2.4cm,"
        r"headsep=0.6cm}"
    )

    # 用户既有的文档规范：仿宋正文、黑体加粗、单倍行距、首行缩进 2 字符
    lines.append(r"\setCJKmainfont{FandolFang-Regular.otf}[BoldFont=FandolHei-Regular.otf]")
    lines.append(r"\linespread{1.0}")
    lines.append(r"\setlength{\parindent}{2em}")
    lines.append(r"\setlength{\parskip}{0.35em}")

    # 与 exam-zh 同名同义的三个宏（题干清理函数会产出它们）
    lines.append(
        r"\newcommand{\fillin}{\underline{\hspace{2.4cm}}}"
    )
    lines.append(r"\newcommand{\paren}{\;\textnormal{（\hspace{1.6em}）}}")
    lines.append(
        r"\newcommand{\questionnumber}[1]{\textbf{#1.}}"
    )
    lines.append(r"\everymath{\displaystyle}")

    page_header = f"错题本 · {subject_label}"
    lines.append(r"\pagestyle{fancy}")
    lines.append(r"\fancyhf{}")
    lines.append(rf"\fancyhead[L]{{\small {page_header}}}")
    if batch_date:
        lines.append(rf"\fancyhead[R]{{\small {batch_date}}}")
    lines.append(r"\fancyfoot[C]{\small 第 \thepage\ 页}")
    lines.append(r"\renewcommand{\headrulewidth}{0.4pt}")

    lines.append(r"\begin{document}")
    lines.append(rf"\zihao{{{opts.font_size}}}")

    # ---- 页首信息 ----
    lines.append(r"\begin{center}")
    lines.append(rf"  {{\zihao{{3}}\bfseries {title or '错题本'}}}\\[4pt]")
    meta_bits = [subject_label]
    if batch_date:
        meta_bits.append(batch_date)
    if student_name:
        meta_bits.append(student_name)
    meta_bits.append(f"共 {len(records)} 题")
    lines.append(rf"  {{\small {' · '.join(meta_bits)}}}")
    lines.append(r"\end{center}")
    lines.append(r"\vspace{0.4em}")
    lines.append(r"\hrule")
    lines.append(r"\vspace{0.8em}")

    if not records:
        lines.append(r"\begin{center}\bfseries 本批次没有需要收录的题目。\end{center}")

    # ---- 逐题 ----
    blocked: list[str] = []
    for index, record in enumerate(records, start=1):
        original_no = str(record.get("question_no") or "").strip()
        header = rf"\questionnumber{{{index}}}"
        if opts.show_original_number and original_no:
            header += rf"\ {{\small（原卷 {original_no}）}}"
        if opts.include_reason:
            reasons = str(record.get("error_reason") or "").strip()
            if reasons:
                reason_text = reasons.replace(",", " / ")
                header += rf"\quad{{\small\bfseries［{reason_text}］}}"
        lines.append(r"\noindent " + header)
        lines.append(r"\par\vspace{0.2em}")

        raw_content = str(record.get("content") or "").strip()
        content = raw_content
        inline_images: set[str] = set()
        if content:
            if opts.include_figures:
                # 正文内联的图要收进 image_paths，否则 xelatex 在编译目录里找不到这个文件。
                content, inline_paths = _extract_inline_images(content, static_dir)
                inline_images = set(inline_paths)
                for path in inline_paths:
                    if path not in image_paths:
                        image_paths.append(path)
            else:
                # 「包含图形」关掉时，题面里内联的图也得一起摘掉 —— 否则这个开关名不副实。
                content = _INLINE_IMAGE_RE.sub("", content)
            content = _strip_placeholder_marks(content).strip()
        if content:
            stem, choice_items = _split_choices_block(_escape_bare_specials(content))
            if stem:
                cleaned_stem = clean_content_for_latex(
                    stem, q_type=str(record.get("question_type") or "")
                )
                lines.append(cleaned_stem)
                lines.append(r"\par")
            if choice_items:
                lines.append(r"\vspace{0.3em}")
                lines.extend(_render_choices(choice_items))
        elif not raw_content:
            lines.append(r"\textit{（本题面尚未识别，可对照题块图复习）}")
            lines.append(r"\par")

        if opts.include_figures:
            figures = []
            for raw in record.get("figure_images") or []:
                resolved = resolve_upload_asset(raw, static_dir)
                if resolved is None:
                    continue
                # 正文里已经内联了同一张图 → 题后不再重复印一遍（一份图印两次）。
                if str(resolved) in inline_images:
                    continue
                figures.append(resolved)
                image_paths.append(str(resolved))
            if figures:
                lines.append(r"\vspace{0.4em}")
                lines.extend(_render_figure_block(figures))

        space = opts.solution_space_cm
        if space > 0:
            lines.append(rf"\vspace{{{space}cm}}")
        lines.append(r"\vspace{0.6em}")

    # ---- 解析页 ----
    answer_count = 0
    if opts.answer_mode != ANSWER_MODE_NONE:
        answer_entries: list[tuple[int, dict]] = []
        for index, record in enumerate(records, start=1):
            source = str(record.get("answer_source") or "none").strip().lower()
            has_manual = bool(record.get("answer_images"))
            has_ai = bool(str(record.get("answer_markdown") or "").strip())
            # 人工解析不限于截图：老师也可能直接敲一段文字解析。只按「有没有截图」判断
            # 会把这类解析静默丢掉，所以人工来源改为「截图或文字，有其一即算就绪」。
            manual_ready = has_manual or has_ai
            if opts.answer_mode == ANSWER_MODE_MANUAL:
                if source == ANSWER_MODE_MANUAL and manual_ready:
                    answer_entries.append((index, record))
            elif opts.answer_mode == ANSWER_MODE_AI:
                if source == ANSWER_MODE_AI and has_ai:
                    # AI 解析必须人工核对过才允许进 PDF
                    if record.get("answer_reviewed"):
                        answer_entries.append((index, record))
                    else:
                        blocked.append(
                            str(record.get("question_no") or index)
                        )
            else:  # both —— manual 与 ai 都收，AI 单仍需核对
                if source == ANSWER_MODE_MANUAL and manual_ready:
                    answer_entries.append((index, record))
                elif source == ANSWER_MODE_AI and has_ai:
                    if record.get("answer_reviewed"):
                        answer_entries.append((index, record))
                    else:
                        blocked.append(str(record.get("question_no") or index))

        if answer_entries:
            lines.append(r"\clearpage")
            lines.append(r"\begin{center}")
            lines.append(r"  {\zihao{3}\bfseries 参考答案与解析}")
            lines.append(r"\end{center}")
            lines.append(r"\vspace{0.3em}")
            lines.append(r"\hrule")
            lines.append(r"\vspace{0.8em}")
            for index, record in answer_entries:
                original_no = str(record.get("question_no") or "").strip()
                head = rf"\questionnumber{{{index}}}"
                if opts.show_original_number and original_no:
                    head += rf"\ {{\small（原卷 {original_no}）}}"
                lines.append(r"\noindent " + head + r"\par\vspace{0.2em}")
                body, images = _render_answer_body(record, static_dir)
                lines.extend(body)
                image_paths.extend(images)
                lines.append(r"\vspace{0.9em}")
                answer_count += 1

    lines.append(r"\end{document}")
    lines.append("")

    return HandoutBuildResult(
        tex_content="\n".join(lines),
        image_paths=sorted(set(image_paths)),
        included_count=len(records),
        answer_count=answer_count,
        blocked_ai_answers=blocked,
    )
