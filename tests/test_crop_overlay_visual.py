"""契约测试：两个裁图弹窗的选框视觉 —— 只有线条，没有四角圆点。

背景（2026-09-15 用户要求）：
    原话「也不需要截图的四角圆点显示，圆点太大了影响观感，正常截出来就是线条组成的
    框框就好了」，并拍板：
      1. 四角圆点**彻底删掉**（不做「透明但可点」的障眼法）；
      2. 框内淡色填充保留；
      3. 错题本与题库工作台两边一起改；
      4.「本题范围」只删文字标签，琥珀虚线参考框保留。

本文件卡三件事：
    1. 两边选框里都不存在任何手柄元素（data-mr-handle / data-handle / rounded-full）；
    2. 缩放能力没有随圆点一起消失 —— 各自保留坐标命中函数作为唯一入口，
       且不再读 DOM 手柄（避免留下看不见的可点区域）；
    3. 删的是「圆点」，不是「能力」：这一点必须由断言守住，
       否则下次有人顺手把缩放算法也删掉，测试全绿但功能没了。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = PROJECT_ROOT / "static" / "index.html"
MISTAKE_JS_PATH = PROJECT_ROOT / "static" / "js" / "mistake.js"
IMPORT_JS_PATH = PROJECT_ROOT / "static" / "js" / "import.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(source: str, marker: str) -> str:
    """抠出 ``<div id="marker" ...>`` 到它的闭合 ``</div>``（选框已无子元素）。"""
    start = source.find(f'id="{marker}"')
    assert start > 0, f"未找到 {marker}"
    end = source.find("</div>", start)
    assert end > start, f"{marker} 结构异常"
    return source[start:end + len("</div>")]


# ----------------- 1. 圆点必须彻底消失 -----------------


def test_mistake_overlay_has_no_corner_handles() -> None:
    block = _block(_read(HTML_PATH), "mrCropSelectRect")
    for handle in ("nw", "ne", "sw", "se"):
        assert f'data-mr-handle="{handle}"' not in block, f"{handle} 手柄应已删除"
    assert "rounded-full" not in block, "错题本选框内不应再有任何圆点"


def test_pdf_overlay_has_no_corner_handles() -> None:
    block = _block(_read(HTML_PATH), "pdfCropOverlayRect")
    for handle in ("nw", "ne", "sw", "se"):
        assert f'data-handle="{handle}"' not in block, f"{handle} 手柄应已删除"
    assert "rounded-full" not in block, "题库选框内不应再有任何圆点"


def test_no_handle_elements_anywhere_in_index_html() -> None:
    """全局兜底：整份首页里一个手柄元素都不许留（防止只改了一半）。"""
    html = _read(HTML_PATH)
    assert "data-mr-handle" not in html, "首页仍残留 data-mr-handle"
    assert "data-handle" not in html, "首页仍残留 data-handle"


# ----------------- 2. 线条与填充保留 -----------------


def test_both_overlays_keep_line_and_fill() -> None:
    html = _read(HTML_PATH)
    for marker in ("mrCropSelectRect", "pdfCropOverlayRect"):
        block = _block(html, marker)
        assert "border-brand-500" in block, f"{marker} 需保留线条边框"
        assert "bg-brand-500/25" in block, f"{marker} 需保留框内淡色填充（用户明确要求保留）"


# ----------------- 3. 缩放能力不丢 -----------------


def test_mistake_resize_uses_coordinate_hit_test() -> None:
    """错题本侧：圆点删掉后，缩放改由 mrCropHitHandle 纯坐标判定。"""
    src = _read(MISTAKE_JS_PATH)
    assert "const MR_CROP_HANDLE_HIT = 10;" in src, "缺少角命中半径常量"
    assert "function mrCropHitHandle(point)" in src, "缺少角坐标命中函数"
    assert "mrCropHitHandle(point)" in src, "mousedown 必须走坐标命中"
    assert "data-mr-handle" not in src, "不应再读已删除的 DOM 手柄"
    # 缩放分支本身必须还在（删圆点≠删缩放）
    assert "mode: 'resize'" in src, "缩放分支不应随圆点一起被删"
    assert "drag.mode === 'resize'" in src, "缩放的移动分支不应被删"


def test_pdf_resize_uses_coordinate_hit_test() -> None:
    """题库侧：hitCropHandle 原本是兜底，现在是缩放的唯一入口。"""
    src = _read(IMPORT_JS_PATH)
    assert "function hitCropHandle(" in src, "缺少角坐标命中函数"
    assert "const handle = hitCropHandle(x, y);" in src, "mousedown 必须走坐标命中"
    assert "e.target.dataset.handle" not in src, "不应再读已删除的 DOM 手柄"
    assert "cropDragMode = 'resize'" in src, "缩放分支不应随圆点一起被删"
    assert "computeCropResize" in src, "缩放算法不应被删"


def test_hover_cursor_is_the_only_affordance() -> None:
    """圆点没了之后，光标是「拖到角上能缩放」的唯一提示 —— 两边都要给。"""
    mistake_src = _read(MISTAKE_JS_PATH)
    assert "function onMrCropHover(event)" in mistake_src, "错题本缺少悬停光标反馈"
    assert "nwse-resize" in mistake_src and "nesw-resize" in mistake_src, "错题本缺少缩放光标"

    import_src = _read(IMPORT_JS_PATH)
    assert "hoverHandle" in import_src, "题库缺少悬停角命中"
    assert "nwse-resize" in import_src and "nesw-resize" in import_src, "题库缺少缩放光标"


# ----------------- 4. 本题范围：删字不删框 -----------------


def test_block_rect_keeps_dashed_box_but_drops_label() -> None:
    """用户只说不要「字」，所以琥珀虚线框必须还在，只是不再写标签。"""
    html = _read(HTML_PATH)
    block = _block(html, "mrCropBlockRect")
    assert "border-dashed" in block, "本题范围虚线框本体应保留"
    assert "border-amber-400/80" in block, "本题范围虚线框配色应保留"
    assert "本题范围" not in block, "标签文字应已删除"

    # 框本体仍要被真实画出来，别只剩一个 hidden 的空壳
    src = _read(MISTAKE_JS_PATH)
    assert "function mrCropBlockPixels()" in src, "缺少本题范围定位函数"
    assert "mrCropBlockRect" in src, "本题范围框未被使用"
