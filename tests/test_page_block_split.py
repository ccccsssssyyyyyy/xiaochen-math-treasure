"""离线切块（mathbank.page_block_split）单元测试。

全部用合成图，不依赖真实扫描件 —— 真实样本的行为用可视化人工核验
（见实施计划 §12 端到端验收），这里只锁死可判定的算法契约：

- 题间距明显大于行间距 → 按题切开
- 行间距均匀（题间距不显著）→ 不硬切，但必须给出足够密的吸附点
- 空白页 / 噪声不报错，返回空块
- 页眉页脚这类「孤立且矮」的块被丢弃
"""
import pytest
from PIL import Image, ImageDraw

from mathbank.page_block_split import (
    COLUMN_DOUBLE,
    COLUMN_SINGLE,
    LAYOUT_SOURCE_MANUAL,
    LAYOUT_SOURCE_TEXT,
    LAYOUT_SOURCE_VISUAL,
    BlockRegion,
    ColumnLayout,
    PageSplitError,
    analyze_page_image,
    boxes_to_blocks,
    crop_region,
    detect_column_layout_from_image,
    detect_column_layout_from_text_rows,
    extract_pdf_text_rows,
    manual_column_layout,
    merge_manual_boxes,
    render_source_to_pages,
)

PAGE_W = 600
PAGE_H = 1200


def _draw_lines(path, lines, *, width=PAGE_W, height=PAGE_H):
    """生成白底黑条页图；lines 为若干 (top, bottom) 像素区间。"""

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for top, bottom in lines:
        draw.rectangle([60, top, width - 60, bottom], fill=0)
    image.save(path, format="PNG")
    return path


# ----------------- 按题切开 -----------------


def test_splits_three_questions_by_wide_gaps(tmp_path):
    """题内 14 px、题间 54 px → 应切出 3 个块。"""

    path = _draw_lines(
        tmp_path / "three.png",
        [
            (100, 120), (134, 154),          # 题 1 两行
            (208, 228), (242, 262),          # 题 2 两行
            (316, 336),                      # 题 3 一行
        ],
    )
    analysis = analyze_page_image(path, page_no=1)

    assert len(analysis.blocks) == 3
    assert [block.block_index for block in analysis.blocks] == [0, 1, 2]
    # 块纵向范围互不重叠且有序
    for previous, current in zip(analysis.blocks, analysis.blocks[1:]):
        assert previous.y_end <= current.y_start
    # 三块应大致覆盖三组文本行
    assert analysis.blocks[0].y_start < 100 / PAGE_H < analysis.blocks[0].y_end
    assert analysis.blocks[2].y_start < 336 / PAGE_H < analysis.blocks[2].y_end


def test_gap_candidates_mark_the_real_boundaries(tmp_path):
    """题间间隙应进入候选切点，行内间隙不应进入。"""

    path = _draw_lines(
        tmp_path / "candidates.png",
        [(100, 120), (134, 154), (208, 228), (242, 262), (316, 336)],
    )
    analysis = analyze_page_image(path, page_no=1)

    # 候选点数量远少于吸附点（吸附点是「全部行间隙」）
    assert 0 < len(analysis.gap_candidates) < len(analysis.snap_points)
    # 候选点应落在题 1/题 2 之间（约 y=181）
    middle = 181 / PAGE_H
    assert any(abs(candidate - middle) < 0.02 for candidate in analysis.gap_candidates)


# ----------------- 行间距均匀：不硬切，但给足吸附点 -----------------


def test_uniform_spacing_stays_one_block_but_offers_snap_points(tmp_path):
    """行间距均匀时切不开 —— 这是已知边界，必须退化为「给足吸附点」。"""

    lines = [(100 + index * 40, 120 + index * 40) for index in range(6)]
    path = _draw_lines(tmp_path / "uniform.png", lines)
    analysis = analyze_page_image(path, page_no=1)

    assert len(analysis.blocks) == 1
    # 6 行 → 5 个行间隙，全部可作为吸附点
    assert len(analysis.snap_points) == 5
    for point in analysis.snap_points:
        assert 0.0 < point < 1.0
    # 吸附点必须落在行与行之间（不会切到字上）
    sorted_lines = sorted(lines)
    for point in analysis.snap_points:
        y = point * PAGE_H
        assert all(not (top - 1 <= y <= bottom + 1) for top, bottom in sorted_lines)


def test_snap_points_present_even_when_nothing_is_split(tmp_path):
    """没有候选切点时，吸附池仍然可用（人工补切的唯一依据）。"""

    lines = [(200 + index * 30, 220 + index * 30) for index in range(4)]
    path = _draw_lines(tmp_path / "dense.png", lines)
    analysis = analyze_page_image(path, page_no=2)

    assert analysis.page_no == 2
    assert len(analysis.snap_points) == 3


# ----------------- 边界情况 -----------------


def test_blank_page_returns_no_blocks_without_raising(tmp_path):
    """整页空白（扫描失败）不该让整批任务崩，返回 0 块即可。"""

    image = Image.new("L", (PAGE_W, PAGE_H), 255)
    path = tmp_path / "blank.png"
    image.save(path, format="PNG")

    analysis = analyze_page_image(path, page_no=3)

    assert analysis.blocks == []
    assert analysis.line_count == 0
    assert analysis.snap_points == []


def test_tiny_speck_is_dropped_as_noise(tmp_path):
    """几个像素的污点不构成题块。"""

    path = _draw_lines(tmp_path / "speck.png", [(500, 502), (503, 505)])
    analysis = analyze_page_image(path, page_no=1)

    assert analysis.blocks == []


def test_header_and_footer_are_dropped(tmp_path):
    """页眉页脚：自身矮、且与正文隔得极远。"""

    path = _draw_lines(
        tmp_path / "header_footer.png",
        [
            (20, 40),                        # 页眉
            (200, 220), (234, 254),          # 正文两行
            (1160, 1180),                    # 页脚
        ],
    )
    analysis = analyze_page_image(path, page_no=1)

    assert analysis.ignored_block_count == 2
    assert len(analysis.blocks) == 1
    block = analysis.blocks[0]
    assert block.y_start * PAGE_H < 200 < block.y_end * PAGE_H


def test_missing_file_raises_page_split_error(tmp_path):
    with pytest.raises(PageSplitError):
        analyze_page_image(tmp_path / "not_here.png")


# ----------------- 裁图 -----------------


def test_crop_region_writes_expected_size(tmp_path):
    path = _draw_lines(tmp_path / "crop_src.png", [(300, 360), (420, 480)])
    out = tmp_path / "block_0.png"

    result = crop_region(path, out, y_start=0.2, y_end=0.35)

    assert result == out
    with Image.open(out) as cropped:
        assert cropped.height == int(0.35 * PAGE_H) - int(0.2 * PAGE_H)
        assert cropped.width == PAGE_W


def test_crop_region_supports_horizontal_window(tmp_path):
    path = _draw_lines(tmp_path / "crop_src2.png", [(300, 360)])
    out = tmp_path / "figure.png"

    crop_region(path, out, y_start=0.2, y_end=0.35, x_start=0.5, x_end=1.0)

    with Image.open(out) as cropped:
        assert cropped.width == PAGE_W // 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"y_start": -0.1, "y_end": 0.5},
        {"y_start": 0.5, "y_end": 0.5},
        {"y_start": 0.6, "y_end": 0.4},
        {"y_start": 0.1, "y_end": 0.5, "x_start": 0.9, "x_end": 0.2},
    ],
)
def test_crop_region_rejects_bad_geometry(tmp_path, kwargs):
    path = _draw_lines(tmp_path / "crop_bad.png", [(300, 360)])
    with pytest.raises(PageSplitError):
        crop_region(path, tmp_path / "never.png", **kwargs)


# ----------------- 渲染入口 -----------------


def test_single_image_source_becomes_one_page(tmp_path):
    """单页 / 单题模式：一张图就是一个批次，切块结果为若干块。"""

    source = _draw_lines(tmp_path / "single.jpg", [(100, 120), (300, 320)])
    pages = render_source_to_pages(source, tmp_path / "pages")

    assert len(pages) == 1
    assert pages[0].name == "page_1.png"
    with Image.open(pages[0]) as rendered:
        assert rendered.mode == "RGB"


def test_unsupported_suffix_is_rejected(tmp_path):
    bad = tmp_path / "note.txt"
    bad.write_text("not an image", encoding="utf-8")

    with pytest.raises(PageSplitError):
        render_source_to_pages(bad, tmp_path / "pages")


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(PageSplitError):
        render_source_to_pages(tmp_path / "ghost.pdf", tmp_path / "pages")


# ----------------- 栏结构判定（分栏支持） -----------------
#
# 判据：横向覆盖率剖面上的「中缝」。合成图必须画成**接近真实卷面**的比例
# （中缝约 2% 页宽），否则缝两侧的取窗区间会落进缝里 —— 见
# COLUMN_FLANK_INNER/OUTER 的说明。

COLUMN_W = 600
COLUMN_H = 1200
#: 左栏 / 右栏的像素范围，中缝 12 px ≈ 2% 页宽（与真实样卷 1%–2% 同量级）
LEFT_COLUMN = (60, 294)
RIGHT_COLUMN = (306, 540)


def _draw_two_columns(path, *, rows=14, line=12, pitch=64):
    """双栏页：左右各若干行短横条，中间留一条窄缝。"""

    image = Image.new("L", (COLUMN_W, COLUMN_H), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    for _ in range(rows):
        draw.rectangle([LEFT_COLUMN[0], top, LEFT_COLUMN[1], top + line], fill=0)
        draw.rectangle([RIGHT_COLUMN[0], top, RIGHT_COLUMN[1], top + line], fill=0)
        top += pitch
    image.save(path, format="PNG")
    return path


def _draw_half_page(path, *, rows=14, line=12, pitch=64):
    """内容只占左半页（扫描偏移的常见形态）：右侧整片留白。"""

    image = Image.new("L", (COLUMN_W, COLUMN_H), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    for _ in range(rows):
        draw.rectangle([60, top, 294, top + line], fill=0)
        top += pitch
    image.save(path, format="PNG")
    return path


def _draw_segmented_rows(path, *, rows=14, line=12, pitch=64):
    """单栏（通栏）但每行被切成 4 段 —— 选项横排 / 表格行。

    这是「一行是否被切成两段」这类判据的典型误判来源：每行都有多段，
    旧实现会把这类单栏页判成双栏。
    """

    image = Image.new("L", (COLUMN_W, COLUMN_H), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    for _ in range(rows):
        for start in (60, 200, 340, 460):
            draw.rectangle([start, top, start + 70, top + line], fill=0)
        top += pitch
    image.save(path, format="PNG")
    return path


def test_image_detects_double_column(tmp_path):
    path = _draw_two_columns(tmp_path / "double.png")
    layout = detect_column_layout_from_image(Image.open(path).convert("L"))

    assert layout.mode == COLUMN_DOUBLE
    assert layout.source == LAYOUT_SOURCE_VISUAL
    assert 0.40 <= layout.boundary <= 0.60
    assert layout.confidence >= 0.40


def test_image_detects_single_column_for_full_width_lines(tmp_path):
    path = _draw_lines(tmp_path / "single.png", [(100, 112), (164, 176), (228, 240)])
    layout = detect_column_layout_from_image(Image.open(path).convert("L"))

    assert layout.mode == COLUMN_SINGLE
    assert layout.boundary == 1.0
    assert not layout.is_double


def test_image_treats_half_page_content_as_single(tmp_path):
    """右侧整片留白不是「中缝」—— 缝两侧必须都成栏才算双栏。"""

    path = _draw_half_page(tmp_path / "half.png")
    layout = detect_column_layout_from_image(Image.open(path).convert("L"))

    assert layout.mode == COLUMN_SINGLE


def test_image_treats_segmented_rows_as_single(tmp_path):
    """每行多段的通栏页不得判成双栏（旧判据正是在这里翻车）。"""

    path = _draw_segmented_rows(tmp_path / "segmented.png")
    layout = detect_column_layout_from_image(Image.open(path).convert("L"))

    assert layout.mode == COLUMN_SINGLE


def test_text_rows_short_segments_are_single():
    """文本层同理：每行只有窄段（选项横排 / 表格行）时不得判成双栏。

    这类页的「缝」正好落在段与段之间，两侧覆盖率都不低，光看覆盖率会误判。
    """

    rows = []
    for index in range(10):
        top = 0.10 + index * 0.07
        for start in (0.06, 0.30, 0.54, 0.78):
            rows.append((start, start + 0.14, top, top + 0.04))

    layout = detect_column_layout_from_text_rows(rows)

    assert layout is not None
    assert layout.mode == COLUMN_SINGLE


def test_text_rows_detect_double_column_and_single_column():
    left = [(0.06, 0.48, 0.10 + i * 0.05, 0.13 + i * 0.05) for i in range(8)]
    right = [(0.51, 0.93, 0.10 + i * 0.05, 0.13 + i * 0.05) for i in range(8)]

    layout = detect_column_layout_from_text_rows([*left, *right])

    assert layout is not None
    assert layout.mode == COLUMN_DOUBLE
    assert layout.source == LAYOUT_SOURCE_TEXT
    assert 0.40 <= layout.boundary <= 0.60


def test_text_rows_wide_lines_are_single():
    """通栏长行（选项 A/B/C/D 排在同一行）必须判成单栏。"""

    rows = []
    for index in range(8):
        top = 0.10 + index * 0.05
        rows.append((0.02, 0.93, top, top + 0.03))          # 题干通栏
        rows.append((0.06, 0.20, top + 0.03, top + 0.045))  # 选项 A
        rows.append((0.45, 0.59, top + 0.03, top + 0.045))  # 选项 C

    layout = detect_column_layout_from_text_rows(rows)

    assert layout is not None
    assert layout.mode == COLUMN_SINGLE


def test_text_rows_too_few_returns_none():
    """样本太少时不下结论，交给图像判栏。"""

    assert detect_column_layout_from_text_rows([]) is None
    assert (
        detect_column_layout_from_text_rows(
            [(0.06, 0.48, 0.1, 0.13), (0.51, 0.93, 0.1, 0.13)]
        )
        is None
    )


# ----------------- 整页切块：按栏 -----------------


def test_double_page_blocks_are_per_column(tmp_path):
    path = _draw_two_columns(tmp_path / "double.png")
    analysis = analyze_page_image(path, page_no=1)

    assert analysis.layout.is_double
    boundary = analysis.layout.boundary
    left = [b for b in analysis.blocks if b.column_index == 1]
    right = [b for b in analysis.blocks if b.column_index == 2]

    assert left and right
    assert all(b.x_start == 0.0 and abs(b.x_end - boundary) < 1e-9 for b in left)
    assert all(abs(b.x_start - boundary) < 1e-9 and b.x_end == 1.0 for b in right)
    # 阅读顺序：左栏在前
    assert [b.block_index for b in analysis.blocks] == list(range(len(analysis.blocks)))
    assert max(b.block_index for b in left) < min(b.block_index for b in right)
    # 每栏的块纵向有序且不重叠
    for column in (left, right):
        for previous, current in zip(
            sorted(column, key=lambda b: b.y_start),
            sorted(column, key=lambda b: b.y_start)[1:],
        ):
            assert previous.y_end <= current.y_start


def test_double_page_snap_points_are_per_column(tmp_path):
    """按栏吸附池必须分开 —— 左右栏的行位置不同，混用会把切线吸到隔壁栏的间隙。"""

    path = _draw_two_columns(tmp_path / "double.png")
    analysis = analyze_page_image(path, page_no=1)

    snaps = analysis.column_snap_points
    assert set(snaps) == {1, 2}
    assert snaps[1] and snaps[2]


def test_manual_single_layout_overrides_double_detection(tmp_path):
    """人工把双栏页判成单栏时，块回到整页宽、栏号清零。"""

    path = _draw_two_columns(tmp_path / "double.png")
    analysis = analyze_page_image(
        path, page_no=1, layout=manual_column_layout(COLUMN_SINGLE)
    )

    assert analysis.layout.mode == COLUMN_SINGLE
    assert analysis.layout.source == LAYOUT_SOURCE_MANUAL
    assert analysis.blocks
    assert all(b.column_index == 0 for b in analysis.blocks)
    assert all(b.x_start == 0.0 and b.x_end == 1.0 for b in analysis.blocks)


def test_manual_double_without_boundary_is_completed(tmp_path):
    """只表态「双栏」没给分栏线时，用自动检测的缝补上，不退回单栏。"""

    path = _draw_two_columns(tmp_path / "double.png")
    layout = manual_column_layout(COLUMN_DOUBLE)
    assert not layout.is_double  # 占位态：还没分栏线

    analysis = analyze_page_image(path, page_no=1, layout=layout)

    assert analysis.layout.mode == COLUMN_DOUBLE
    assert analysis.layout.source == LAYOUT_SOURCE_MANUAL
    assert 0.0 < analysis.layout.boundary < 1.0
    assert all(b.column_index in (1, 2) for b in analysis.blocks)


# ----------------- 双栏夹具 -----------------


def _double_layout(boundary=0.5):
    return ColumnLayout(
        mode=COLUMN_DOUBLE, boundary=boundary, source=LAYOUT_SOURCE_MANUAL
    )


# ----------------- 文本行提取 -----------------


def test_extract_pdf_text_rows_ignores_non_pdf(tmp_path):
    image = _draw_lines(tmp_path / "page.png", [(100, 120)])
    assert extract_pdf_text_rows(image) == {}
    assert extract_pdf_text_rows(tmp_path / "ghost.pdf") == {}


def test_extract_pdf_text_rows_reads_lines_and_detects_columns(tmp_path):
    """行级坐标：双栏 PDF 应被提取成左右两组窄行，并判成双栏。"""

    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    # 用会折行的整段文字，而不是 insert_text 的单行短串 —— 短串只占 0.18 页宽，
    # 既不像真卷的栏宽，也过不了「缝两侧要各有成栏的一段」那条判据（实测 0.408）。
    paragraph = (
        "A block of running text long enough to wrap several times inside the column, "
        "which is what a real exam page looks like. "
    ) * 6
    # 左栏 x 40–285、右栏 x 310–555（栏宽约 0.41 页宽，中缝 25pt 约 4% 页宽）
    page.insert_textbox(pymupdf.Rect(40, 60, 285, 400), paragraph, fontsize=9)
    page.insert_textbox(pymupdf.Rect(310, 60, 555, 400), paragraph, fontsize=9)
    path = tmp_path / "two_column.pdf"
    document.save(path)
    document.close()

    rows = extract_pdf_text_rows(path)
    assert 1 in rows
    assert len(rows[1]) >= 8
    # 行级：每行都是窄行，不会把左右栏合并成一个通栏块
    assert all((x1 - x0) < 0.60 for x0, x1, _y0, _y1 in rows[1])
    # 左右两侧各自都拿得出「成栏宽度」的行（判双栏的另一半依据）
    assert max(x1 - x0 for x0, x1, _y0, _y1 in rows[1] if x1 <= 0.5) >= 0.25
    assert max(x1 - x0 for x0, x1, _y0, _y1 in rows[1] if x0 >= 0.5) >= 0.25

    layout = detect_column_layout_from_text_rows(rows[1])
    assert layout is not None
    assert layout.mode == COLUMN_DOUBLE
    assert 0.40 <= layout.boundary <= 0.60


# ----------------- 人工框选：框 → 题块 → 与自动块合并 -----------------


def _region(index, y_start, y_end, *, x_start=0.0, x_end=1.0, column_index=0, page_no=1):
    return BlockRegion(
        page_no=page_no,
        block_index=index,
        y_start=y_start,
        y_end=y_end,
        x_start=x_start,
        x_end=x_end,
        column_index=column_index,
    )


def test_boxes_to_blocks_drops_degenerate_boxes():
    """退化框（反向坐标、不足最小边长、非数字）不该产出零面积块。"""

    blocks = boxes_to_blocks(
        1,
        [
            (0.2, 0.50, 0.1, 0.50),      # x 反向且零宽 → 丢
            (0.0, 0.10, 0.005, 0.40),    # 宽 0.005 < 最小边长 → 丢
            ("x", 0.10, 0.50, 0.20),     # 非数字 → 丢
            (None,),                     # 结构不对 → 丢
            (0.0, 0.10, 0.50, 0.20),     # 合法
        ],
    )
    assert len(blocks) == 1
    assert (blocks[0].y_start, blocks[0].y_end) == (pytest.approx(0.10), pytest.approx(0.20))


def test_boxes_to_blocks_swaps_reversed_coordinates():
    """坐标写反（反向拖出来的框）就地纠正，而不是把框丢掉。"""

    blocks = boxes_to_blocks(1, [(0.6, 0.30, 0.2, 0.10)])
    assert len(blocks) == 1
    assert (blocks[0].x_start, blocks[0].x_end) == (pytest.approx(0.2), pytest.approx(0.6))
    assert (blocks[0].y_start, blocks[0].y_end) == (pytest.approx(0.10), pytest.approx(0.30))


def test_boxes_to_blocks_infers_column_index_from_boundary():
    """双栏页按横向范围推断栏号：左 1、右 2、横跨 0。"""

    blocks = boxes_to_blocks(
        1,
        [
            (0.0, 0.10, 0.48, 0.20),     # 左栏
            (0.52, 0.10, 1.0, 0.20),     # 右栏
            (0.0, 0.40, 1.0, 0.50),      # 横跨两栏
        ],
        column_boundary=0.5,
    )
    assert [(b.column_index, round(b.y_start, 2)) for b in blocks] == [(1, 0.10), (0, 0.40), (2, 0.10)]
    # 没给分栏线时一律记 0（跨栏 / 不适用）
    assert [b.column_index for b in boxes_to_blocks(1, [(0.0, 0.1, 0.48, 0.2)])] == [0]


def test_boxes_to_blocks_orders_left_column_first_then_by_y():
    """双栏页：左栏块整体排在右栏之前，栏内按 y 升序（＝阅读顺序）。"""

    blocks = boxes_to_blocks(
        1,
        [
            (0.52, 0.80, 1.0, 0.90),     # 右栏靠下
            (0.0, 0.60, 0.48, 0.70),     # 左栏靠下
            (0.52, 0.10, 1.0, 0.20),     # 右栏靠上
            (0.0, 0.10, 0.48, 0.20),     # 左栏靠上
        ],
        column_boundary=0.5,
    )
    assert [(b.column_index, round(b.y_start, 2)) for b in blocks] == [
        (1, 0.10), (1, 0.60), (2, 0.10), (2, 0.80),
    ]
    assert [b.block_index for b in blocks] == [0, 1, 2, 3]


def test_merge_manual_boxes_merges_fragments_into_one_block():
    """框住 3 个碎片 → 只剩这 1 个框块（治「切得太细」的主场景）。"""

    auto = [_region(0, 0.10, 0.14), _region(1, 0.15, 0.19), _region(2, 0.20, 0.24)]
    merged = merge_manual_boxes(auto, [(0.0, 0.09, 1.0, 0.25)], page_no=1)
    assert len(merged) == 1
    assert (merged[0].y_start, merged[0].y_end) == (pytest.approx(0.09), pytest.approx(0.25))
    assert merged[0].block_index == 0


def test_merge_manual_boxes_keeps_untouched_blocks():
    """没被框压到的自动块原样保留，只是按位置重新编号。"""

    auto = [_region(0, 0.10, 0.14), _region(1, 0.50, 0.60)]
    merged = merge_manual_boxes(auto, [(0.0, 0.09, 1.0, 0.16)], page_no=1)
    assert [(round(b.y_start, 2), round(b.y_end, 2)) for b in merged] == [(0.09, 0.16), (0.50, 0.60)]
    assert [b.block_index for b in merged] == [0, 1]


def test_merge_manual_boxes_splits_one_block_with_two_boxes():
    """在原块上画两个框 → 原块消失、两个框顶上（治「一块含两题」）。"""

    auto = [_region(0, 0.10, 0.30)]
    merged = merge_manual_boxes(
        auto, [(0.0, 0.10, 1.0, 0.20), (0.0, 0.20, 1.0, 0.30)], page_no=1
    )
    assert [(round(b.y_start, 2), round(b.y_end, 2)) for b in merged] == [(0.10, 0.20), (0.20, 0.30)]


def test_merge_manual_boxes_keeps_uncovered_fragment():
    """框只压住块的下半 → 上半作为残段保留，正文不会被静默丢掉。"""

    auto = [_region(0, 0.10, 0.50)]
    merged = merge_manual_boxes(auto, [(0.0, 0.40, 1.0, 0.60)], page_no=1)
    assert [(round(b.y_start, 2), round(b.y_end, 2)) for b in merged] == [(0.10, 0.40), (0.40, 0.60)]


def test_merge_manual_boxes_drops_noise_fragment_keeps_real_one():
    """残段阈值两侧各测一格：0.75% 丢弃、0.8% 保留。

    阈值从 0.002 提到 0.008 的实测依据：一份 16 页双栏卷上，人工框边擦过临行会留下
    0.01%–0.77% 页高的残段（页面 2340 px 时 0.3–18 px），裁出来的图肉眼看不到东西，
    却各占一张卡片；同卷**最小的真实自动块**占页高 1.33%，与 0.008 之间没有残段落点，
    所以这个值既不误杀真实的一小截内容，又能把噪声条清干净。
    """

    # 框压到 0.0075 → 上侧只剩 0.75% 的噪声条，丢掉，只剩框本身
    merged = merge_manual_boxes([_region(0, 0.0, 0.10)], [(0.0, 0.0075, 1.0, 0.10)], page_no=1)
    assert [(round(b.y_start, 4), round(b.y_end, 4)) for b in merged] == [(0.0075, 0.10)]

    # 框压到 0.008 → 上侧恰好 0.8%（等于阈值，判据是 >=）→ 留下
    merged = merge_manual_boxes([_region(0, 0.0, 0.10)], [(0.0, 0.008, 1.0, 0.10)], page_no=1)
    assert [(round(b.y_start, 4), round(b.y_end, 4)) for b in merged] == [
        (0.0, 0.008),
        (0.008, 0.10),
    ]


def test_merge_manual_boxes_ignores_edge_touch():
    """边界相接（重叠恰为 0）不算压住 —— 否则相邻块的框会互相啃掉对方。"""

    auto = [_region(0, 0.10, 0.20)]
    merged = merge_manual_boxes(auto, [(0.0, 0.20, 1.0, 0.30)], page_no=1)
    assert len(merged) == 2
    assert (merged[0].y_start, merged[0].y_end) == (pytest.approx(0.10), pytest.approx(0.20))


def test_merge_manual_boxes_ignores_box_in_other_column():
    """右栏的框不该动到左栏的块 —— 双栏页两侧的 y 范围天然重叠。"""

    auto = [
        _region(0, 0.10, 0.40, x_start=0.0, x_end=0.48),
        _region(1, 0.10, 0.40, x_start=0.52, x_end=1.0, column_index=2),
    ]
    merged = merge_manual_boxes(
        auto, [(0.55, 0.15, 0.95, 0.35)], page_no=1, column_boundary=0.5
    )
    spans = [(round(b.y_start, 2), round(b.y_end, 2)) for b in merged]
    assert spans[0] == (0.10, 0.40), "左栏整块不该被右栏的框切到"
    assert spans[1:] == [(0.10, 0.15), (0.15, 0.35), (0.35, 0.40)]


def test_merge_manual_boxes_empty_list_restores_auto_result():
    """空框列表＝清除人工框，必须精确复原（含块号）。

    否则用户删掉一个画错的框、重算一次，后面所有块号都会漂移 —— 块图文件名
    跟着变，页面上的对错标记也就对不上了。
    """

    auto = [_region(0, 0.10, 0.14), _region(1, 0.50, 0.60), _region(2, 0.70, 0.80)]
    assert merge_manual_boxes(auto, [], page_no=1) == auto
    assert merge_manual_boxes(auto, [None, "junk"], page_no=1) == auto


def test_merge_manual_boxes_renumbers_contiguously():
    """合并后块号必须是 0..n-1 连续 —— 它同时兼作块图文件名。"""

    auto = [_region(3, 0.10, 0.14), _region(7, 0.30, 0.40), _region(9, 0.60, 0.70)]
    merged = merge_manual_boxes(auto, [(0.0, 0.08, 1.0, 0.16)], page_no=1)
    assert [b.block_index for b in merged] == list(range(len(merged)))
