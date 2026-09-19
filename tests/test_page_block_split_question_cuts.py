"""题号切点（PDF 文本层语义切点）单元测试。

背景与算法说明见 ``mathbank/page_block_split.py`` 的「题号切点」一节：行投影只认
几何，密排单栏卷的题间距（实测最大 18 px）小于强间隙阈值（页高 2339 px 时 21 px），
一整页 5 道题会粘成 1 块。这里用「行首是题号」的文本行做语义切点补上。

本文件锁死四件事：

1. 文本层的读取（带文字的那份与判栏那份坐标必须完全一致）；
2. 题号识别的**挡误报**规则 —— 章节号 / 续行单位 / 答案册三族同形词都不许进；
3. 切点落到「题号行**自己**紧上方那个空白缝」：文本层的 y0 是字形框上缘、墨迹上缘
   比它低一截（真实卷中位 13.8 px），所以要先认出「哪条行带是这一行」再切；认不出
   就一刀不切，**没有**「退一步切上一行」这条路径（那会把上一题最后一行切进来）；
4. 拿不到文本层时行为与从前逐字一致 —— 这条通道只会加切点，不会改坏已切对的页。
"""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mathbank.page_block_split import (
    _question_cut_points,
    _question_rest_is_plausible,
    analyze_page_image,
    extract_pdf_text_lines,
    extract_pdf_text_rows,
    render_source_to_pages,
)

PAGE_W = 600
PAGE_H = 1200

#: 密排单栏页：行距 40 px、行高 20 px → 行间隙 19 px。
#: 阈值：min_strong = max(6, int(1200 * 0.009)) = 10，而 base_gap = 19 →
#: strong_gap = max(10, int(19 * 2.2)) = 41 > 19 → **一页切不开**（正是要治的病）。
DENSE_TOPS = [100 + index * 40 for index in range(8)]
DENSE_LINE_H = 20
DENSE_TEXTS = [
    "1. 一物体做匀加速直线运动",
    "加速度随时间均匀增大",
    "则该物体的速度变化量为",
    "2. 关于匀变速直线运动",
    "下列判断正确的是",
    "物体的加速度保持不变",
    "3. 某物体运动的 v-t 图象",
    "图线的斜率表示加速度",
]
#: 题号分别落在第 0 / 3 / 6 行 → 第 0 行上方没有空白缝（页顶）不计，
#: 另两个切点落在 (200,220) 与 (320,340) 两个行间隙的中点。
EXPECTED_DENSE_CUTS = [0.175, 0.275]

#: 真实样卷（用户本机数据，不进仓库；CI 上跳过）。
REAL_SAMPLE = (
    Path(__file__).resolve().parents[1] / "static" / "uploads" / "mistakes" / "1" / "source.pdf"
)


def _draw_lines(path, lines, *, width=PAGE_W, height=PAGE_H):
    """白底黑条页图；lines 为若干 (top, bottom) 像素区间。"""

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for top, bottom in lines:
        draw.rectangle([60, top, width - 60, bottom], fill=0)
    image.save(path, format="PNG")
    return path


def _text_lines(tops, texts, *, x0=0.06, x1=0.92, line_h=DENSE_LINE_H):
    """把 (行顶像素, 文字) 组装成文本层口径的归一化行。"""

    return [
        (x0, x1, top / PAGE_H, (top + line_h) / PAGE_H, text)
        for top, text in zip(tops, texts)
    ]


def _rows_of(lines):
    return [(x0, x1, y0, y1) for x0, x1, y0, y1, _text in lines]


# ----------------- 文本层读取 -----------------


def test_extract_pdf_text_lines_ignores_non_pdf(tmp_path):
    image = _draw_lines(tmp_path / "page.png", [(100, 120)])
    assert extract_pdf_text_lines(image) == {}
    assert extract_pdf_text_lines(tmp_path / "ghost.pdf") == {}


def test_extract_pdf_text_lines_carries_text_and_shares_rows_coordinates(tmp_path):
    """带文字的那份必须与判栏那份**同源**：同一次读取、同一套过滤、同坐标。"""

    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    # 用内置中文字体，让中文题干真的进得了文本层（否则断行/去空之后啥也不剩）
    page.insert_text((60, 100), "1. 一物体做匀加速直线运动", fontname="china-s", fontsize=11)
    page.insert_text((60, 130), "2. 关于加速度的说法", fontname="china-s", fontsize=11)
    path = tmp_path / "questions.pdf"
    document.save(path)
    document.close()

    lines = extract_pdf_text_lines(path)
    assert 1 in lines
    texts = [text for *_box, text in lines[1]]
    assert any(text.startswith("1.") for text in texts)
    assert any(text.startswith("2.") for text in texts)
    # 坐标一字不差，否则判栏与切点会看到两份不同的版面
    assert _rows_of(lines[1]) == extract_pdf_text_rows(path)[1]


# ----------------- 题号识别的挡误报规则 -----------------


@pytest.mark.parametrize(
    ("rest", "expected"),
    [
        (" 一物体做匀加速直线运动", True),
        ("2021年4月天和核心舱发射成功", True),   # 题面以年份开头 → 是真题号
        (" 关于加速度的说法正确的是", True),
        ("1 加速度", False),                     # 章节号 1.5.1 的尾段
        ("20 m/s,则他在全程中的平均速度为", False),  # 续行的单位/数值
        ("tls", False),                          # 数字后跟字母
        ("[A][B]I .[D]", False),                 # 答案册的作答行
        ("", False),
    ],
)
def test_question_rest_plausibility(rest, expected):
    assert _question_rest_is_plausible(rest) is expected


def test_question_cut_points_follows_left_margin_and_monotonic_numbers():
    """题号必须在左边界、必须递增、且跳号不超过 5。"""

    rows = [
        (0.06, 0.92, 0.10, 0.12, "1. 第一题"),
        (0.30, 0.92, 0.20, 0.22, "2. 缩进到页中的题号不算"),
        (0.06, 0.92, 0.30, 0.32, "1. 重复题号"),
        (0.06, 0.92, 0.40, 0.42, "8. 跳号过大"),
        (0.06, 0.92, 0.50, 0.52, "3. 正常递增"),
    ]

    assert _question_cut_points(rows) == [(0.10, 0.12), (0.50, 0.52)]


def test_question_cut_points_rejects_short_line_and_oversized_number():
    """选项横排那样的窄行、以及 30 以上的编号（答案解析里的序号）都不算题号。"""

    rows = [
        (0.06, 0.12, 0.10, 0.12, "1. 太窄"),
        (0.06, 0.92, 0.20, 0.22, "31. 超出题号范围"),
        (0.06, 0.92, 0.30, 0.32, "2. 合法"),
    ]

    assert _question_cut_points(rows) == [(0.30, 0.32)]


def test_question_cut_points_empty_input():
    assert _question_cut_points([]) == []


# ----------------- 整页切块：密排单栏 -----------------


def test_dense_single_column_page_is_one_block_without_text_lines(tmp_path):
    """没有文本层（纯扫描件）时保持原样 —— 这是本功能的回归护栏。"""

    path = _draw_lines(
        tmp_path / "dense.png",
        [(top, top + DENSE_LINE_H) for top in DENSE_TOPS],
    )
    analysis = analyze_page_image(path, page_no=1)

    assert len(analysis.blocks) == 1
    assert analysis.question_cut_points == []


def test_question_numbers_split_a_dense_single_column_page(tmp_path):
    """同一页给了带文字的文本层 → 按题号切成 3 块。"""

    path = _draw_lines(
        tmp_path / "dense.png",
        [(top, top + DENSE_LINE_H) for top in DENSE_TOPS],
    )
    lines = _text_lines(DENSE_TOPS, DENSE_TEXTS)

    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(lines), text_lines=lines
    )

    assert len(analysis.blocks) == 3
    assert analysis.question_cut_points == pytest.approx(EXPECTED_DENSE_CUTS)
    # 阅读顺序自上而下，块纵向不重叠
    for previous, current in zip(analysis.blocks, analysis.blocks[1:]):
        assert previous.y_end <= current.y_start


def test_question_cut_lands_in_the_blank_gap_not_on_ink(tmp_path):
    """切点必须落在行与行之间 —— 落在行上就会把字切成两半。"""

    drawn = [(top, top + DENSE_LINE_H) for top in DENSE_TOPS]
    path = _draw_lines(tmp_path / "dense.png", drawn)
    lines = _text_lines(DENSE_TOPS, DENSE_TEXTS)

    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(lines), text_lines=lines
    )

    assert analysis.question_cut_points
    for point in analysis.question_cut_points:
        y = point * PAGE_H
        assert all(not (top <= y <= bottom) for top, bottom in drawn)
    # 切点必然是某个吸附点（吸附点＝全部行间隙中点，两者同一套算法）
    assert set(analysis.question_cut_points) <= set(analysis.snap_points)


def test_question_row_without_a_gap_above_is_not_cut(tmp_path):
    """题号行的墨迹与上方内容粘成一条行带（中间没有白缝）→ 不切。

    锚定法认不出「这条行带是题号行本人」（它起得比字形框上缘还早），于是宁可少切
    一刀，也不退到上一行的缝上去 —— 那正是「上一题选项被框进来」的成因。
    """

    # (200,240) 与 (241,280) 相接 → 行投影把它俩合成一条文本行 (200,280)
    path = _draw_lines(tmp_path / "glued.png", [(100, 120), (200, 240), (241, 280)])
    # 题号行标在合并行的内部（y0=241）：上方最近的空白缝底部是 200 → 差 41 > 24
    lines = [(0.06, 0.92, 241 / PAGE_H, 280 / PAGE_H, "2. 关于加速度的说法")]

    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(lines), text_lines=lines
    )

    assert analysis.question_cut_points == []
    assert len(analysis.blocks) == 1


def test_cut_anchors_on_the_question_rows_own_ink_band(tmp_path):
    """字形框上缘比墨迹上缘高一截（真实卷中位 13.8 px）时，切点必须落在**题号行自己**
    上方那道缝上，不得退到上一行的缝上 —— 退过去就会把上一题的选项框进本题。"""

    # 上一题末行 (100,120)、本题题号行 (160,180)
    path = _draw_lines(tmp_path / "offset.png", [(40, 60), (100, 120), (160, 180)])
    lines = [
        (0.06, 0.92, 40 / PAGE_H, 60 / PAGE_H, "1. 上一题"),
        (0.06, 0.92, 100 / PAGE_H, 120 / PAGE_H, "A. 上一题的选项"),
        # 题号行的字形框上缘比墨迹上缘高 14 px
        (0.06, 0.92, 146 / PAGE_H, 195 / PAGE_H, "2. 本题"),
    ]

    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(lines), text_lines=lines
    )

    # 唯一生效的切点 = (120, 160) 那道缝的中点；页顶那一行没有缝可切
    assert [round(point * PAGE_H) for point in analysis.question_cut_points] == [140]
    # 本题所在块的顶界必须在上一行墨迹之下
    hosts = [block for block in analysis.blocks if block.y_start * PAGE_H > 120]
    assert hosts and hosts[0].y_start * PAGE_H >= 120


def test_question_row_glued_into_a_photo_band_is_not_cut(tmp_path):
    """题号行的墨迹跟上方内容粘在同一条行带里（真实卷里是右侧照片把行间空白吃光）
    → 不切：那里没有白缝，硬切会切穿图形。"""

    # 一整块连续墨迹（照片），题号行的字形框落在它内部
    path = _draw_lines(tmp_path / "photo.png", [(100, 400)])
    lines = [(0.06, 0.92, 300 / PAGE_H, 360 / PAGE_H, "2. 题干紧挨着照片")]

    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(lines), text_lines=lines
    )

    assert analysis.question_cut_points == []
    assert len(analysis.blocks) == 1


# ----------------- 整页切块：双栏按栏归属 -----------------

COLUMN_W = 600
COLUMN_H = 1200
LEFT_COLUMN = (60, 294)
RIGHT_COLUMN = (306, 540)
COLUMN_ROWS = 14
COLUMN_PITCH = 64
COLUMN_LINE = 12
#: 左栏第 0 / 4 / 8 / 12 行是题号行；第 0 行在页顶、上方无缝隙 → 只生效 3 个切点
COLUMN_QUESTION_ROWS = (0, 4, 8, 12)


def _draw_two_columns(path):
    image = Image.new("L", (COLUMN_W, COLUMN_H), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    for _ in range(COLUMN_ROWS):
        draw.rectangle([LEFT_COLUMN[0], top, LEFT_COLUMN[1], top + COLUMN_LINE], fill=0)
        draw.rectangle([RIGHT_COLUMN[0], top, RIGHT_COLUMN[1], top + COLUMN_LINE], fill=0)
        top += COLUMN_PITCH
    image.save(path, format="PNG")
    return path


def test_double_column_question_cuts_stay_inside_their_own_column(tmp_path):
    """左栏的题号不得把右栏切开 —— 两栏的行位置天然错开。"""

    path = _draw_two_columns(tmp_path / "double.png")
    left_lines = [
        (
            LEFT_COLUMN[0] / COLUMN_W,
            LEFT_COLUMN[1] / COLUMN_W,
            (60 + row * COLUMN_PITCH) / COLUMN_H,
            (60 + row * COLUMN_PITCH + COLUMN_LINE) / COLUMN_H,
            f"{index + 1}. 左栏第 {row + 1} 行的题号",
        )
        for index, row in enumerate(COLUMN_QUESTION_ROWS)
    ]

    plain = analyze_page_image(path, page_no=1)
    analysis = analyze_page_image(
        path, page_no=1, text_rows=_rows_of(left_lines), text_lines=left_lines
    )

    assert plain.layout.is_double and analysis.layout.is_double
    # 不给文本层：左右各 1 块（栏内行距均匀，切不开）
    assert len(plain.blocks) == 2
    # 给了左栏题号：左栏 4 块 + 右栏 1 块
    assert len(analysis.blocks) == 5
    assert len([b for b in analysis.blocks if b.column_index == 2]) == 1
    assert len(analysis.question_cut_points) == 3
    # 切点落在左栏内（分栏线左侧）
    boundary = analysis.layout.boundary
    assert all(0.0 < point < 1.0 for point in analysis.question_cut_points)
    assert all(b.x_end <= boundary + 1e-9 for b in analysis.blocks if b.column_index == 1)


# ----------------- 真实样卷回归 -----------------


@pytest.mark.skipif(not REAL_SAMPLE.is_file(), reason="本机没有该真实样卷（CI 上跳过）")
def test_real_sample_single_column_pages_get_question_cuts(tmp_path):
    """真实 16 页卷：单栏密排页被切开，双栏页最多只多 1 块（不误切）。

    断言的数字来自 200 dpi（``DEFAULT_RENDER_DPI``）渲染下的实测结果，
    与生产路径同一套渲染参数。
    """

    lines = extract_pdf_text_lines(REAL_SAMPLE)
    assert lines, "样卷应带文本层"
    rows = {page_no: _rows_of(page_lines) for page_no, page_lines in lines.items()}
    pages = render_source_to_pages(REAL_SAMPLE, tmp_path / "pages", dpi=200)

    counts = {}
    for page_no, page_path in enumerate(pages, start=1):
        before = analyze_page_image(page_path, page_no=page_no, text_rows=rows.get(page_no))
        after = analyze_page_image(
            page_path,
            page_no=page_no,
            text_rows=rows.get(page_no),
            text_lines=lines.get(page_no),
        )
        counts[page_no] = (len(before.blocks), len(after.blocks), after)
        # 切点只能加块、不能减块
        assert len(after.blocks) >= len(before.blocks)

    # 用户报告的那页：6 道题所在页从 1 块变 5 块（第 4 题题号在文本层里不可用）
    assert counts[15][:2] == (1, 5)
    assert len(counts[15][2].question_cut_points) == 4
    # 下一页：10 道题，8 个题号可用 → 9 块
    assert counts[16][:2] == (1, 9)
    assert len(counts[16][2].question_cut_points) == 8

    # 双栏页不得被题号切点切碎：每页最多多 1 块
    for page_no, (before, after, analysis) in counts.items():
        if analysis.layout.is_double:
            assert after - before <= 1, f"p{page_no} 双栏页被切碎：{before} → {after}"
